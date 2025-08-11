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


def enhance_edge_with_context(edge_data: dict, episode_content: str) -> dict:
    """
    Enhance edge data with language detection and cultural context.
    
    Args:
        edge_data: Basic edge/relationship information
        episode_content: Episode content to analyze for context
        
    Returns:
        Enhanced edge data with language and cultural information
    """
    # Analyze the episode content for language and cultural context
    context_analysis = LanguageDetector.analyze_text_context(episode_content)
    
    # Add cultural context description if available
    if not edge_data.get('cultural_context'):
        cultural_desc = LanguageDetector.generate_cultural_context_description(context_analysis)
        if cultural_desc:
            edge_data['cultural_context'] = cultural_desc
            
    # Set confidence based on language clarity
    if not edge_data.get('confidence'):
        # Higher confidence for relationships in detected languages
        if context_analysis.get('language'):
            edge_data['confidence'] = 0.9
        else:
            edge_data['confidence'] = 0.8
            
    # Set interaction type based on content analysis
    if not edge_data.get('interaction_type'):
        if any(greeting in episode_content.lower() for greeting in ['hello', 'hola', 'hi', 'bonjour']):
            edge_data['interaction_type'] = 'GREETING'
        elif '?' in episode_content:
            edge_data['interaction_type'] = 'QUESTION_ANSWER'
        elif context_analysis.get('language'):
            edge_data['interaction_type'] = 'CONVERSATION'
        else:
            edge_data['interaction_type'] = 'INFORMATION_EXCHANGE'
            
    return edge_data


class Edge(BaseModel):
    relation_type: str = Field(..., description='FACT_PREDICATE_IN_SCREAMING_SNAKE_CASE')
    source_entity_id: int = Field(..., description='The id of the source entity of the fact.')
    target_entity_id: int = Field(..., description='The id of the target entity of the fact.')
    fact: str = Field(..., description='Detailed description of the relationship or interaction')
    confidence: float = Field(
        default=1.0,
        description='Confidence score (0.0-1.0) for this relationship based on evidence strength'
    )
    interaction_type: str | None = Field(
        default=None,
        description='Type of interaction: CONVERSATION, INTRODUCTION, INFORMATION_EXCHANGE, QUESTION_ANSWER, GREETING, RECALL, etc.'
    )
    cultural_context: str | None = Field(
        default=None,
        description='Cultural or linguistic context of the interaction (e.g., "Spanish conversation", "formal register", "friendly tone")'
    )
    valid_at: str | None = Field(
        None,
        description='The date and time when the relationship described by the edge fact became true or was established. Use ISO 8601 format (YYYY-MM-DDTHH:MM:SS.SSSSSSZ)',
    )
    invalid_at: str | None = Field(
        None,
        description='The date and time when the relationship described by the edge fact stopped being true or ended. Use ISO 8601 format (YYYY-MM-DDTHH:MM:SS.SSSSSSZ)',
    )


class ExtractedEdges(BaseModel):
    edges: list[Edge]


class MissingFacts(BaseModel):
    missing_facts: list[str] = Field(..., description="facts that weren't extracted")


class Prompt(Protocol):
    edge: PromptVersion
    reflexion: PromptVersion
    extract_attributes: PromptVersion


class Versions(TypedDict):
    edge: PromptFunction
    reflexion: PromptFunction
    extract_attributes: PromptFunction


def edge(context: dict[str, Any]) -> list[Message]:
    return [
        Message(
            role='system',
            content='You are an expert relationship extractor with cultural and linguistic awareness that extracts detailed fact triples from text. '
            '1. Extract relationships with granular detail including interaction types and cultural context. '
            '2. Preserve cultural and linguistic markers in relationship descriptions. '
            '3. Treat the CURRENT TIME as the time the CURRENT MESSAGE was sent. All temporal information should be extracted relative to this time.',
        ),
        Message(
            role='user',
            content=f"""
<FACT TYPES>
{context['edge_types']}
</FACT TYPES>

<PREVIOUS_MESSAGES>
{to_prompt_json([ep for ep in context['previous_episodes']], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
</PREVIOUS_MESSAGES>

<CURRENT_MESSAGE>
{context['episode_content']}
</CURRENT_MESSAGE>

<ENTITIES>
{context['nodes']} 
</ENTITIES>

<REFERENCE_TIME>
{context['reference_time']}  # ISO 8601 (UTC); used to resolve relative time mentions
</REFERENCE_TIME>

# ENHANCED RELATIONSHIP EXTRACTION TASK
Extract all factual relationships between the given ENTITIES based on the CURRENT MESSAGE with enhanced granularity and cultural awareness.
Only extract facts that:
- involve two DISTINCT ENTITIES from the ENTITIES list,
- are clearly stated or unambiguously implied in the CURRENT MESSAGE,
    and can be represented as edges in a knowledge graph.
- The FACT TYPES provide a list of the most important types of facts, make sure to extract facts of these types
- The FACT TYPES are not an exhaustive list, extract all facts from the message even if they do not fit into one
    of the FACT TYPES
- The FACT TYPES each contain their fact_type_signature which represents the source and target entity types.

You may use information from the PREVIOUS MESSAGES only to disambiguate references or support continuity.


{context['custom_prompt']}

# ENHANCED EXTRACTION RULES

1. **Entity Matching**: Only emit facts where both the subject and object match IDs in ENTITIES.
2. **Distinct Entities**: Each fact must involve two **distinct** entities.
3. **Granular Relation Types**: Use specific SCREAMING_SNAKE_CASE strings that capture the nuance of the interaction:
   - Instead of generic "INTERACTS_WITH", use "GREETS", "INTRODUCES_SELF", "ASKS_QUESTION", "PROVIDES_INFORMATION", "RECALLS_INFORMATION"
   - Instead of "COMMUNICATES_WITH", use "SPEAKS_IN_LANGUAGE", "CONVERSES_FORMALLY", "CONVERSES_INFORMALLY"
   - Examples: INTRODUCES_SELF_TO, GREETS_IN_LANGUAGE, RECALLS_PREVIOUS_INTERACTION, PROVIDES_LANGUAGE_CONTEXT
4. **Detailed Facts**: The `fact` field should provide rich detail including cultural and linguistic context.
5. **Confidence Scoring**: Set confidence based on how explicit the relationship is (1.0 = explicitly stated, 0.8 = clearly implied, 0.6 = inferred).
6. **Interaction Types**: Classify the type of interaction (CONVERSATION, INTRODUCTION, INFORMATION_EXCHANGE, etc.).
7. **Cultural Context**: Capture language use, formality level, cultural markers in the cultural_context field.
8. **Source Fidelity**: Quote or closely paraphrase the original source sentence(s) in the fact.
9. **No Duplication**: Do not emit duplicate or semantically redundant facts.
10. **Temporal Anchoring**: Use `REFERENCE_TIME` to resolve vague or relative temporal expressions.
11. **Evidence-Based**: Do **not** hallucinate or infer temporal bounds from unrelated events.

# DATETIME RULES

- Use ISO 8601 with “Z” suffix (UTC) (e.g., 2025-04-30T00:00:00Z).
- If the fact is ongoing (present tense), set `valid_at` to REFERENCE_TIME.
- If a change/termination is expressed, set `invalid_at` to the relevant timestamp.
- Leave both fields `null` if no explicit or resolvable time is stated.
- If only a date is mentioned (no time), assume 00:00:00.
- If only a year is mentioned, use January 1st at 00:00:00.

# ENHANCED RELATIONSHIP CATEGORIES

**Communication Relationships**:
- GREETS_IN_LANGUAGE, INTRODUCES_SELF_TO, CONVERSES_WITH, ASKS_QUESTION_TO, RESPONDS_TO
- SPEAKS_LANGUAGE_WITH, USES_FORMAL_REGISTER_WITH, USES_INFORMAL_REGISTER_WITH

**Information Relationships**:
- PROVIDES_INFORMATION_TO, REQUESTS_INFORMATION_FROM, RECALLS_INFORMATION_ABOUT
- SHARES_KNOWLEDGE_WITH, EXPLAINS_TO, CLARIFIES_FOR

**Social Relationships**:
- MAINTAINS_CONVERSATION_WITH, ESTABLISHES_RAPPORT_WITH, SHOWS_INTEREST_IN
- DEMONSTRATES_FAMILIARITY_WITH, EXPRESSES_PREFERENCE_TO

**Temporal Relationships**:
- INTERACTED_PREVIOUSLY_WITH, CONTINUES_CONVERSATION_WITH, RESUMES_DISCUSSION_WITH
        """,
        ),
    ]


def reflexion(context: dict[str, Any]) -> list[Message]:
    sys_prompt = """You are an AI assistant that determines which facts have not been extracted from the given context"""

    user_prompt = f"""
<PREVIOUS MESSAGES>
{to_prompt_json([ep for ep in context['previous_episodes']], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
</PREVIOUS MESSAGES>
<CURRENT MESSAGE>
{context['episode_content']}
</CURRENT MESSAGE>

<EXTRACTED ENTITIES>
{context['nodes']}
</EXTRACTED ENTITIES>

<EXTRACTED FACTS>
{context['extracted_facts']}
</EXTRACTED FACTS>

Given the above MESSAGES, list of EXTRACTED ENTITIES entities, and list of EXTRACTED FACTS; 
determine if any facts haven't been extracted.
"""
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def extract_attributes(context: dict[str, Any]) -> list[Message]:
    return [
        Message(
            role='system',
            content='You are a culturally-aware assistant that extracts and enhances fact properties with linguistic and cultural context from the provided text.',
        ),
        Message(
            role='user',
            content=f"""

        <MESSAGE>
        {to_prompt_json(context['episode_content'], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
        </MESSAGE>
        <REFERENCE TIME>
        {context['reference_time']}
        </REFERENCE TIME>

        Given the above MESSAGE, its REFERENCE TIME, and the following FACT, update any of its attributes based on the information provided
        in MESSAGE. Use the provided attribute descriptions to better understand how each attribute should be determined.

        Guidelines:
        1. **Evidence-Based**: Do not hallucinate entity property values if they cannot be found in the current context.
        2. **Source Fidelity**: Only use the provided MESSAGES and FACT to set attribute values.
        3. **Enhanced Attributes**: Update fact with:
           - Confidence score based on evidence strength
           - Interaction type classification
           - Cultural and linguistic context markers
           - Temporal anchoring using REFERENCE TIME
        4. **Cultural Preservation**: Capture language use, formality, cultural markers in cultural_context.
        5. **Interaction Classification**: Set appropriate interaction_type (CONVERSATION, INTRODUCTION, etc.).
        6. **Confidence Assessment**: Score based on explicitness (1.0 = direct, 0.8 = implied, 0.6 = inferred).

        <FACT>
        {context['fact']}
        </FACT>
        """,
        ),
    ]


versions: Versions = {
    'edge': edge,
    'reflexion': reflexion,
    'extract_attributes': extract_attributes,
}
