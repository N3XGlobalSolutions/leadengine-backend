"""Realistic conversation scenarios for stress testing."""

import random
from typing import List, Dict


class ConversationScenarios:
    """Generate realistic conversation patterns for testing."""
    
    GREETINGS = [
        "Hi there!",
        "Hello",
        "Hey, I have some questions",
        "Good morning",
        "Hi, I'm interested in your services",
    ]
    
    GENERAL_QUESTIONS = [
        "What services do you offer?",
        "Can you tell me about your pricing?",
        "What are your business hours?",
        "Do you offer consultations?",
        "How long have you been in business?",
        "What makes you different from competitors?",
        "Can you help me with my project?",
        "Do you have any case studies?",
    ]
    
    QUALIFICATION_RESPONSES = [
        "I'm looking for a solution within the next month",
        "My budget is around $5,000 to $10,000",
        "We're a team of about 20 people",
        "Yes, we've used similar services before",
        "I'm the decision maker for this project",
        "We need this implemented within 3 months",
        "Our company is in the tech industry",
        "We're based in New York",
    ]
    
    CONTACT_INFO = [
        "My name is John Smith",
        "You can reach me at john@example.com",
        "My phone is +1-555-0100",
        "I'm Sarah Johnson, email sarah.j@company.com",
        "Call me at 555-0199",
        "Michael Chen, +1-555-0188",
    ]
    
    FOLLOW_UP_QUESTIONS = [
        "That sounds great! What's the next step?",
        "Interesting. Can you tell me more?",
        "How does the implementation process work?",
        "What kind of support do you provide?",
        "Do you have any customer testimonials?",
        "Thanks! When can we get started?",
    ]
    
    @classmethod
    def generate_conversation(cls, num_messages: int = 6) -> List[str]:
        """
        Generate a realistic conversation flow.
        
        Args:
            num_messages: Number of messages to generate
            
        Returns:
            List of message strings
        """
        messages = []
        
        # Start with a greeting
        messages.append(random.choice(cls.GREETINGS))
        
        # Add questions and responses
        for i in range(1, num_messages):
            if i == 1:
                # Second message is usually a question
                messages.append(random.choice(cls.GENERAL_QUESTIONS))
            elif i == num_messages - 1:
                # Last message is often contact info or follow-up
                if random.random() > 0.5:
                    messages.append(random.choice(cls.CONTACT_INFO))
                else:
                    messages.append(random.choice(cls.FOLLOW_UP_QUESTIONS))
            else:
                # Mix of responses and questions
                msg_type = random.choice(['question', 'response', 'contact'])
                if msg_type == 'question':
                    messages.append(random.choice(cls.GENERAL_QUESTIONS))
                elif msg_type == 'response':
                    messages.append(random.choice(cls.QUALIFICATION_RESPONSES))
                else:
                    messages.append(random.choice(cls.CONTACT_INFO))
        
        return messages
    
    @classmethod
    def generate_edge_case_conversation(cls) -> List[str]:
        """Generate conversation with edge cases (long messages, special chars)."""
        return [
            "Hi! " + "I really need help with understanding " * 10,  # Long message
            "What about pricing for: Option A, Option B, and Option C?",  # Complex
            "Testing special chars: @#$%^&*()!",
            "My email is test+user@example.com",  # Plus sign
            "Can you handle unicode? 你好 مرحبا",  # Unicode
        ]
    
    @classmethod
    def generate_batch_conversations(cls, count: int, messages_per_conversation: int = 6) -> List[List[str]]:
        """
        Generate multiple conversations.
        
        Args:
            count: Number of conversations to generate
            messages_per_conversation: Messages per conversation
            
        Returns:
            List of conversations (each conversation is a list of messages)
        """
        conversations = []
        for _ in range(count):
            # 90% normal conversations, 10% edge cases
            if random.random() > 0.1:
                conversations.append(cls.generate_conversation(messages_per_conversation))
            else:
                conversations.append(cls.generate_edge_case_conversation())
        
        return conversations
