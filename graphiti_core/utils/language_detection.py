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

import re
from typing import Dict, List, Optional, Tuple


class LanguageDetector:
    """
    Enhanced language detection and cultural context analysis for entity extraction.
    
    Provides cultural awareness and linguistic pattern recognition to enhance
    entity extraction with proper language and cultural context markers.
    """
    
    # Language detection patterns based on common words and patterns
    LANGUAGE_PATTERNS = {
        'es': {
            'words': ['hola', 'gracias', 'como', 'estas', 'muy', 'bien', 'que', 'soy', 'mi', 'nombre', 'español'],
            'patterns': [r'\b(es|soy|estoy)\b', r'\b(mi|mis|tu|tus)\b', r'\b(muy|tan|bastante)\b'],
            'formality_markers': ['usted', 'señor', 'señora', 'don', 'doña'],
            'informal_markers': ['tú', 'vos', 'che', 'pibe']
        },
        'en': {
            'words': ['hello', 'thank', 'thanks', 'how', 'are', 'you', 'very', 'good', 'what', 'my', 'name', 'english'],
            'patterns': [r'\b(am|is|are)\b', r'\b(my|your|his|her)\b', r'\b(very|quite|rather)\b'],
            'formality_markers': ['sir', 'madam', 'mr', 'mrs', 'ms', 'dr'],
            'informal_markers': ['hey', 'hi', 'yeah', 'ok', 'okay']
        },
        'fr': {
            'words': ['bonjour', 'merci', 'comment', 'vous', 'très', 'bien', 'que', 'suis', 'mon', 'nom', 'français'],
            'patterns': [r'\b(je|tu|il|elle)\b', r'\b(mon|ma|mes|ton|ta|tes)\b', r'\b(très|assez|plutôt)\b'],
            'formality_markers': ['monsieur', 'madame', 'mademoiselle', 'vous'],
            'informal_markers': ['tu', 'salut', 'ciao']
        },
        'de': {
            'words': ['hallo', 'danke', 'wie', 'sind', 'sehr', 'gut', 'was', 'bin', 'mein', 'name', 'deutsch'],
            'patterns': [r'\b(ich|du|er|sie)\b', r'\b(mein|dein|sein|ihr)\b', r'\b(sehr|ziemlich|recht)\b'],
            'formality_markers': ['herr', 'frau', 'sie'],
            'informal_markers': ['du', 'hallo', 'hi']
        },
        'pt': {
            'words': ['olá', 'obrigado', 'como', 'está', 'muito', 'bem', 'que', 'sou', 'meu', 'nome', 'português'],
            'patterns': [r'\b(sou|é|está)\b', r'\b(meu|minha|seu|sua)\b', r'\b(muito|bem|bastante)\b'],
            'formality_markers': ['senhor', 'senhora', 'você'],
            'informal_markers': ['tu', 'oi', 'tchau']
        }
    }

    @classmethod
    def detect_language(cls, text: str) -> Optional[str]:
        """
        Detect the primary language of the given text.
        
        Args:
            text: Text to analyze for language detection
            
        Returns:
            ISO 639-1 language code if detected, None otherwise
        """
        if not text or len(text.strip()) < 3:
            return None
            
        text_lower = text.lower()
        language_scores = {}
        
        for lang_code, patterns in cls.LANGUAGE_PATTERNS.items():
            score = 0
            
            # Score based on word matches
            for word in patterns['words']:
                if word in text_lower:
                    score += 2
                    
            # Score based on pattern matches
            for pattern in patterns['patterns']:
                matches = re.findall(pattern, text_lower)
                score += len(matches)
                
            language_scores[lang_code] = score
            
        # Return language with highest score if above threshold
        if language_scores:
            max_lang = max(language_scores, key=language_scores.get)
            if language_scores[max_lang] >= 2:  # Minimum confidence threshold
                return max_lang
                
        return None

    @classmethod
    def detect_formality_level(cls, text: str, language: Optional[str] = None) -> str:
        """
        Detect the formality level of the text.
        
        Args:
            text: Text to analyze
            language: Detected language (if known)
            
        Returns:
            Formality level: 'formal', 'informal', or 'neutral'
        """
        if not text:
            return 'neutral'
            
        text_lower = text.lower()
        
        # If language is detected, use language-specific markers
        if language and language in cls.LANGUAGE_PATTERNS:
            patterns = cls.LANGUAGE_PATTERNS[language]
            
            formal_score = sum(1 for marker in patterns['formality_markers'] if marker in text_lower)
            informal_score = sum(1 for marker in patterns['informal_markers'] if marker in text_lower)
            
            if formal_score > informal_score:
                return 'formal'
            elif informal_score > formal_score:
                return 'informal'
                
        # General formality indicators
        formal_indicators = ['please', 'thank you', 'sir', 'madam', 'would you', 'could you']
        informal_indicators = ['hey', 'hi', 'yeah', 'ok', 'gonna', 'wanna']
        
        formal_count = sum(1 for indicator in formal_indicators if indicator in text_lower)
        informal_count = sum(1 for indicator in informal_indicators if indicator in text_lower)
        
        if formal_count > informal_count:
            return 'formal'
        elif informal_count > formal_count:
            return 'informal'
            
        return 'neutral'

    @classmethod
    def extract_cultural_markers(cls, text: str, language: Optional[str] = None) -> List[str]:
        """
        Extract cultural and linguistic markers from the text.
        
        Args:
            text: Text to analyze
            language: Detected language (if known)
            
        Returns:
            List of cultural markers found
        """
        markers = []
        
        if not text:
            return markers
            
        # Add language marker if detected
        if language:
            markers.append(f"{language}_speaker")
            
        # Add formality marker
        formality = cls.detect_formality_level(text, language)
        if formality != 'neutral':
            markers.append(f"{formality}_register")
            
        # Language-specific cultural markers
        if language and language in cls.LANGUAGE_PATTERNS:
            patterns = cls.LANGUAGE_PATTERNS[language]
            text_lower = text.lower()
            
            # Check for formal markers
            if any(marker in text_lower for marker in patterns['formality_markers']):
                markers.append("formal_address")
                
            # Check for informal markers  
            if any(marker in text_lower for marker in patterns['informal_markers']):
                markers.append("informal_address")
                
        return list(set(markers))  # Remove duplicates

    @classmethod
    def analyze_text_context(cls, text: str) -> Dict[str, any]:
        """
        Comprehensive analysis of text for language and cultural context.
        
        Args:
            text: Text to analyze
            
        Returns:
            Dictionary containing language, formality, and cultural markers
        """
        language = cls.detect_language(text)
        formality = cls.detect_formality_level(text, language)
        cultural_markers = cls.extract_cultural_markers(text, language)
        
        return {
            'language': language,
            'formality': formality,
            'cultural_markers': cultural_markers,
            'has_cultural_context': bool(language or cultural_markers)
        }

    @classmethod
    def generate_cultural_context_description(cls, analysis: Dict[str, any]) -> Optional[str]:
        """
        Generate a human-readable description of cultural context.
        
        Args:
            analysis: Result from analyze_text_context
            
        Returns:
            Cultural context description or None
        """
        if not analysis.get('has_cultural_context'):
            return None
            
        parts = []
        
        # Add language information
        if analysis.get('language'):
            lang_names = {
                'es': 'Spanish',
                'en': 'English', 
                'fr': 'French',
                'de': 'German',
                'pt': 'Portuguese'
            }
            lang_name = lang_names.get(analysis['language'], analysis['language'])
            parts.append(f"{lang_name} conversation")
            
        # Add formality information
        if analysis.get('formality') and analysis['formality'] != 'neutral':
            parts.append(f"{analysis['formality']} register")
            
        # Add specific cultural markers
        markers = analysis.get('cultural_markers', [])
        specific_markers = [m for m in markers if not m.endswith('_speaker') and not m.endswith('_register')]
        if specific_markers:
            parts.extend(specific_markers)
            
        return ', '.join(parts) if parts else None