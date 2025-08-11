"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, Field

from .models import Message, PromptFunction, PromptVersion
from .prompt_helpers import to_prompt_json
from graphiti_core.utils.language_detection import LanguageDetector


def enhance_entity_with_context(entity_data: dict, episode_content: str) -> dict:
    """
    Enhance entity data with language detection and cultural context.
    
    Args:
        entity_data: Basic entity information
        episode_content: Episode content to analyze for context
        
    Returns:
        Enhanced entity data with language and cultural information
    """
    # Analyze the episode content for language and cultural context
    context_analysis = LanguageDetector.analyze_text_context(episode_content)
    
    # Add language information if detected
    if context_analysis.get('language') and not entity_data.get('language'):
        entity_data['language'] = context_analysis['language']
        
    # Add cultural context description if available
    if not entity_data.get('cultural_context'):
        cultural_desc = LanguageDetector.generate_cultural_context_description(context_analysis)
        if cultural_desc:
            entity_data['cultural_context'] = cultural_desc
            
    # Add disambiguation context for entities mentioned in conversation
    if not entity_data.get('disambiguation') and context_analysis.get('has_cultural_context'):
        entity_data['disambiguation'] = f"Entity from {context_analysis.get('language', 'multilingual')} conversation context"
        
    return entity_data


class ExtractedEntity(BaseModel):
    name: str = Field(..., description='Name of the extracted entity')
    entity_type_id: int = Field(
        description='ID of the classified entity type. '
        'Must be one of the provided entity_type_id integers.',
    )
    language: str | None = Field(
        default=None, 
        description='Primary language code (ISO 639-1) of the entity if applicable (e.g., "es", "en", "fr")'
    )
    cultural_context: str | None = Field(
        default=None,
        description='Cultural or regional context markers (e.g., "Spanish-speaking", "formal register", "professional context")'
    )
    disambiguation: str | None = Field(
        default=None,
        description='Additional context to distinguish this entity from similar ones (e.g., "Fernando mentioned by user", "specific conversation partner")'
    )


class ExtractedEntities(BaseModel):
    extracted_entities: list[ExtractedEntity] = Field(..., description='List of extracted entities')


class MissedEntities(BaseModel):
    missed_entities: list[str] = Field(..., description="Names of entities that weren't extracted")


class EntityClassificationTriple(BaseModel):
    uuid: str = Field(description='UUID of the entity')
    name: str = Field(description='Name of the entity')
    entity_type: str | None = Field(
        default=None, description='Type of the entity. Must be one of the provided types or None'
    )


class EntityClassification(BaseModel):
    entity_classifications: list[EntityClassificationTriple] = Field(
        ..., description='List of entities classification triples.'
    )


class EntitySummary(BaseModel):
    summary: str = Field(
        description='Summary containing the important information about the entity. Under 250 words'
    )


class Prompt(Protocol):
    extract_message: PromptVersion
    extract_json: PromptVersion
    extract_text: PromptVersion
    reflexion: PromptVersion
    classify_nodes: PromptVersion
    extract_attributes: PromptVersion
    extract_summary: PromptVersion


class Versions(TypedDict):
    extract_message: PromptFunction
    extract_json: PromptFunction
    extract_text: PromptFunction
    reflexion: PromptFunction
    classify_nodes: PromptFunction
    extract_attributes: PromptFunction
    extract_summary: PromptFunction


def extract_message(context: dict[str, Any]) -> list[Message]:
    sys_prompt = """You are an AI assistant that extracts entity nodes from conversational messages with enhanced cultural and linguistic awareness. 
    Your primary task is to extract and classify the speaker and other significant entities mentioned in the conversation, paying special attention to language use, cultural context, and disambiguation markers."""

    user_prompt = f"""
<ENTITY TYPES>
{context['entity_types']}
</ENTITY TYPES>

<PREVIOUS MESSAGES>
{to_prompt_json([ep for ep in context['previous_episodes']], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
</PREVIOUS MESSAGES>

<CURRENT MESSAGE>
{context['episode_content']}
</CURRENT MESSAGE>

Instructions:

You are given a conversation context and a CURRENT MESSAGE. Your task is to extract **entity nodes** mentioned **explicitly or implicitly** in the CURRENT MESSAGE with enhanced cultural and linguistic awareness.
Pronoun references such as he/she/they or this/that/those should be disambiguated to the names of the 
reference entities.

1. **Speaker Extraction**: Always extract the speaker (the part before the colon `:` in each dialogue line) as the first entity node.
   - If the speaker is mentioned again in the message, treat both mentions as a **single entity**.

2. **Entity Identification**:
   - Extract all significant entities, concepts, or actors that are **explicitly or implicitly** mentioned in the CURRENT MESSAGE.
   - **Exclude** entities mentioned only in the PREVIOUS MESSAGES (they are for context only).

3. **Entity Classification**:
   - Use the descriptions in ENTITY TYPES to classify each extracted entity.
   - Assign the appropriate `entity_type_id` for each one.

4. **Language and Cultural Analysis**:
   - **Language Detection**: If the entity uses or is associated with a specific language, set the `language` field (ISO 639-1 codes: "en", "es", "fr", "de", etc.)
   - **Cultural Context**: Identify cultural markers such as formality level, regional context, professional setting, or communication style
   - **Disambiguation**: Provide context to distinguish entities (e.g., "Fernando from conversation", "user's conversation partner")

5. **Enhanced Context Preservation**:
   - Consider the conversational register (formal/informal)
   - Note any cultural or linguistic patterns in entity behavior
   - Preserve relationship context for disambiguation

6. **Exclusions**:
   - Do NOT extract entities representing relationships or actions.
   - Do NOT extract dates, times, or other temporal information—these will be handled separately.

7. **Formatting**:
   - Be **explicit and unambiguous** in naming entities (e.g., use full names when available).
   - Include language and cultural context when evident from the conversation.

{context['custom_prompt']}
"""
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def extract_json(context: dict[str, Any]) -> list[Message]:
    sys_prompt = """You are an AI assistant that extracts entity nodes from JSON with cultural and linguistic awareness. 
    Your primary task is to extract and classify relevant entities while preserving cultural context and language information."""

    user_prompt = f"""
<ENTITY TYPES>
{context['entity_types']}
</ENTITY TYPES>

<SOURCE DESCRIPTION>:
{context['source_description']}
</SOURCE DESCRIPTION>
<JSON>
{context['episode_content']}
</JSON>

{context['custom_prompt']}

Given the above source description and JSON, extract relevant entities from the provided JSON with enhanced cultural and linguistic awareness.
For each entity extracted, also determine its entity type based on the provided ENTITY TYPES and their descriptions.
Indicate the classified entity type by providing its entity_type_id.

Guidelines:
1. **Primary Entity Extraction**: Always try to extract entities that the JSON represents (often "name", "user", or key identifier fields).
2. **Cultural Context**: Look for language indicators, cultural markers, or regional information in the JSON data.
3. **Language Detection**: If language information is present or can be inferred, include it in the language field.
4. **Disambiguation**: Use JSON structure and source description to provide disambiguation context.
5. **Date Exclusion**: Do NOT extract any properties that contain dates.
6. **Context Preservation**: Preserve any cultural, linguistic, or contextual information available in the JSON structure.
"""
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def extract_text(context: dict[str, Any]) -> list[Message]:
    sys_prompt = """You are an AI assistant that extracts entity nodes from text with enhanced cultural and linguistic awareness. 
    Your primary task is to extract and classify entities while preserving cultural context, language patterns, and disambiguation markers."""

    user_prompt = f"""
<ENTITY TYPES>
{context['entity_types']}
</ENTITY TYPES>

<TEXT>
{context['episode_content']}
</TEXT>

Given the above text, extract entities from the TEXT that are explicitly or implicitly mentioned with enhanced cultural and linguistic awareness.
For each entity extracted, also determine its entity type based on the provided ENTITY TYPES and their descriptions.
Indicate the classified entity type by providing its entity_type_id.

{context['custom_prompt']}

Guidelines:
1. **Entity Extraction**: Extract significant entities, concepts, or actors mentioned in the text.
2. **Cultural Awareness**: Identify language use, cultural markers, formality levels, and regional context.
3. **Language Detection**: Set language field for entities when language patterns are evident.
4. **Disambiguation**: Provide context to distinguish entities from similar ones.
5. **Relationship Exclusions**: Avoid creating nodes for relationships or actions.
6. **Temporal Exclusions**: Avoid creating nodes for temporal information like dates, times or years (these will be added to edges later).
7. **Explicit Naming**: Be as explicit as possible in node names, using full names and avoiding abbreviations.
8. **Context Preservation**: Include enough cultural and linguistic context for proper entity understanding.
"""
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def reflexion(context: dict[str, Any]) -> list[Message]:
    sys_prompt = """You are an AI assistant that determines which entities have not been extracted from the given context"""

    user_prompt = f"""
<PREVIOUS MESSAGES>
{to_prompt_json([ep for ep in context['previous_episodes']], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
</PREVIOUS MESSAGES>
<CURRENT MESSAGE>
{context['episode_content']}
</CURRENT MESSAGE>

<EXTRACTED ENTITIES>
{context['extracted_entities']}
</EXTRACTED ENTITIES>

Given the above previous messages, current message, and list of extracted entities; determine if any entities haven't been
extracted.
"""
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def classify_nodes(context: dict[str, Any]) -> list[Message]:
    sys_prompt = """You are an AI assistant that classifies entity nodes given the context from which they were extracted"""

    user_prompt = f"""
    <PREVIOUS MESSAGES>
    {to_prompt_json([ep for ep in context['previous_episodes']], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
    </PREVIOUS MESSAGES>
    <CURRENT MESSAGE>
    {context['episode_content']}
    </CURRENT MESSAGE>
    
    <EXTRACTED ENTITIES>
    {context['extracted_entities']}
    </EXTRACTED ENTITIES>
    
    <ENTITY TYPES>
    {context['entity_types']}
    </ENTITY TYPES>
    
    Given the above conversation, extracted entities, and provided entity types and their descriptions, classify the extracted entities.
    
    Guidelines:
    1. Each entity must have exactly one type
    2. Only use the provided ENTITY TYPES as types, do not use additional types to classify entities.
    3. If none of the provided entity types accurately classify an extracted node, the type should be set to None
"""
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def extract_attributes(context: dict[str, Any]) -> list[Message]:
    return [
        Message(
            role='system',
            content='You are a culturally-aware assistant that extracts and updates entity properties with enhanced linguistic and cultural context preservation.',
        ),
        Message(
            role='user',
            content=f"""

        <MESSAGES>
        {to_prompt_json(context['previous_episodes'], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
        {to_prompt_json(context['episode_content'], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
        </MESSAGES>

        <REFERENCE TIME>
        {context.get('reference_time', 'Not provided')}
        </REFERENCE TIME>

        Given the above MESSAGES, REFERENCE TIME, and the following ENTITY, update any of its attributes based on the information provided
        in MESSAGES. Use the provided attribute descriptions to better understand how each attribute should be determined.

        Guidelines:
        1. **Evidence-Based Updates**: Do not hallucinate entity property values if they cannot be found in the current context.
        2. **Source Fidelity**: Only use the provided MESSAGES and ENTITY to set attribute values.
        3. **Enhanced Summary**: The summary attribute should include:
           - Cultural and linguistic context
           - Temporal information using REFERENCE TIME
           - Communication patterns and relationship dynamics
           - Language use and formality levels
           - Summaries must be no longer than 250 words but maximize information density.
        4. **Language Attributes**: Update language field if language patterns are evident in the messages.
        5. **Cultural Context**: Update cultural_context field with communication style, formality, cultural markers.
        6. **Disambiguation**: Update disambiguation field with relationship context and distinguishing information.
        7. **Temporal Anchoring**: Use REFERENCE TIME to provide temporal context in updates.
        
        <ENTITY>
        {context['node']}
        </ENTITY>
        """,
        ),
    ]


def extract_summary(context: dict[str, Any]) -> list[Message]:
    sys_prompt = """You are an AI assistant that generates culturally-aware, temporally-contextualized entity summaries. 
    Your task is to create or update comprehensive summaries that preserve cultural, linguistic, and temporal context."""

    user_prompt = f"""
<ENTITY INFORMATION>
Entity Name: {context['node']['name']}
Current Summary: {context['node']['summary']}
Entity Types: {context['node']['entity_types']}
Current Attributes: {context['node']['attributes']}
</ENTITY INFORMATION>

<EPISODE CONTENT>
{context['episode_content']}
</EPISODE CONTENT>

<PREVIOUS EPISODES>
{context['previous_episodes']}
</PREVIOUS EPISODES>

<REFERENCE TIME>
{context.get('reference_time', 'Not provided')}
</REFERENCE TIME>

Instructions:
1. **Comprehensive Summary**: Create or update a comprehensive summary for this entity based on all available information
2. **Temporal Context**: Include timing and sequence of interactions, using REFERENCE TIME for temporal anchoring
3. **Cultural Preservation**: Preserve cultural markers, language preferences, communication styles, and cultural context
4. **Linguistic Awareness**: Note language use patterns, formality levels, and communication preferences
5. **Relationship Context**: Include relationship dynamics and interaction patterns with other entities
6. **Progressive Enhancement**: If current summary exists, enhance with new information while preserving existing context
7. **Conciseness**: Keep summary under 250 words while maximizing information density
8. **Disambiguation**: Include enough context to distinguish this entity from similar ones

Generate a culturally-aware, temporally-contextualized summary that captures the complete essence of this entity including their cultural background, communication patterns, and relationship dynamics.
    """

    return [
        Message(
            role='system',
            content=sys_prompt,
        ),
        Message(
            role='user', 
            content=user_prompt,
        ),
    ]


versions: Versions = {
    'extract_message': extract_message,
    'extract_json': extract_json,
    'extract_text': extract_text,
    'reflexion': reflexion,
    'classify_nodes': classify_nodes,
    'extract_attributes': extract_attributes,
    'extract_summary': extract_summary,
}
