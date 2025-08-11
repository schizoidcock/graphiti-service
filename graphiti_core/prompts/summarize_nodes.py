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


class Summary(BaseModel):
    summary: str = Field(
        ...,
        description='Culturally-aware, temporally-contextualized summary containing the important information about the entity. Under 250 words',
    )
    temporal_context: str | None = Field(
        default=None,
        description='Temporal context of the entity interactions (e.g., "First mentioned on 2024-01-15", "Ongoing conversation partner")'
    )
    cultural_markers: str | None = Field(
        default=None,
        description='Cultural and linguistic markers associated with the entity (e.g., "Spanish speaker", "formal communication style")'
    )


class SummaryDescription(BaseModel):
    description: str = Field(..., description='One sentence description of the provided summary')


class Prompt(Protocol):
    summarize_pair: PromptVersion
    summarize_context: PromptVersion
    summary_description: PromptVersion


class Versions(TypedDict):
    summarize_pair: PromptFunction
    summarize_context: PromptFunction
    summary_description: PromptFunction


def summarize_pair(context: dict[str, Any]) -> list[Message]:
    return [
        Message(
            role='system',
            content='You are a culturally-aware assistant that combines summaries while preserving temporal and cultural context.',
        ),
        Message(
            role='user',
            content=f"""
        Synthesize the information from the following two summaries into a single succinct summary that preserves both temporal progression and cultural context.
        
        Requirements:
        1. **Temporal Integration**: Merge temporal contexts, maintaining chronological understanding
        2. **Cultural Preservation**: Combine cultural markers and linguistic patterns
        3. **Context Continuity**: Preserve relationship dynamics and interaction patterns
        4. **Conciseness**: Keep summary under 250 words while maximizing information density
        5. **Comprehensive Synthesis**: Create a unified narrative that captures the essence of both summaries

        Summaries:
        {to_prompt_json(context['node_summaries'], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
        
        <REFERENCE TIME>
        {context.get('reference_time', 'Not provided')}
        </REFERENCE TIME>
        """,
        ),
    ]


def summarize_context(context: dict[str, Any]) -> list[Message]:
    return [
        Message(
            role='system',
            content='You are a culturally-aware assistant that creates comprehensive entity summaries with temporal and cultural context preservation.',
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
        
        Given the above MESSAGES, REFERENCE TIME, and the following ENTITY information, create a comprehensive summary for the ENTITY.
        
        **Enhanced Summary Requirements**:
        1. **Source Fidelity**: Use only information from the provided MESSAGES
        2. **Entity Focus**: Include only information relevant to the provided ENTITY
        3. **Temporal Context**: Include timing, sequence, and temporal relationships using REFERENCE TIME
        4. **Cultural Awareness**: Preserve cultural markers, language patterns, communication styles
        5. **Relationship Dynamics**: Capture interaction patterns and relationship context
        6. **Progressive Context**: Build upon existing ENTITY CONTEXT with new information
        7. **Conciseness**: Keep summary under 250 words while maximizing information density
        8. **Disambiguation**: Include enough context to distinguish this entity from similar ones
        
        **Attribute Extraction Guidelines**:
        1. **Evidence-Based**: Do not hallucinate entity property values if they cannot be found in the current context
        2. **Source Accuracy**: Only use provided messages, entity, and entity context to set attribute values
        3. **Temporal Anchoring**: Use REFERENCE TIME for temporal context in attributes
        4. **Cultural Markers**: Extract cultural and linguistic information when available
        
        <ENTITY>
        {context['node_name']}
        </ENTITY>
        
        <ENTITY CONTEXT>
        {context['node_summary']}
        </ENTITY CONTEXT>
        
        <ATTRIBUTES>
        {to_prompt_json(context['attributes'], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
        </ATTRIBUTES>
        """,
        ),
    ]


def summary_description(context: dict[str, Any]) -> list[Message]:
    return [
        Message(
            role='system',
            content='You are a culturally-aware assistant that creates descriptive summaries capturing both informational content and cultural context.',
        ),
        Message(
            role='user',
            content=f"""
        Create a comprehensive one sentence description of the summary that explains:
        1. **Content Type**: What kind of information is summarized
        2. **Cultural Context**: Any cultural or linguistic markers present
        3. **Temporal Scope**: Time range or temporal context covered
        4. **Relationship Context**: Key interaction patterns or relationship dynamics
        
        The description should capture the essence of both the factual content and the cultural/temporal context.
        Keep the description concise but informative.

        Summary:
        {to_prompt_json(context['summary'], ensure_ascii=context.get('ensure_ascii', True), indent=2)}
        
        <REFERENCE TIME>
        {context.get('reference_time', 'Not provided')}
        </REFERENCE TIME>
        """,
        ),
    ]


versions: Versions = {
    'summarize_pair': summarize_pair,
    'summarize_context': summarize_context,
    'summary_description': summary_description,
}
