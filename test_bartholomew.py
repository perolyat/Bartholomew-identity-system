#!/usr/bin/env python3
"""
Simple test script for Bartholomew AI
Tests a single conversation exchange
"""

import sys
from pathlib import Path


# Add the package to path for development
sys.path.insert(0, str(Path(__file__).parent))

from chat import BartholomewChat


def test_bartholomew():
    """Test Bartholomew with a simple conversation"""
    print("🧪 Testing Bartholomew...")

    # Initialize Bartholomew
    chat = BartholomewChat()

    # Test message
    test_message = (
        "Hello Bartholomew! Can you introduce yourself and tell me about your capabilities?"
    )

    print(f"\n🗣️  Test message: {test_message}")
    print("\n🤖 Bartholomew's response:")
    print("=" * 60)

    # Get response
    response = chat.get_response(test_message)
    print(response)

    print("=" * 60)
    print("\n✅ Test completed successfully!")

    # Show some system info
    print("\n📊 System Status:")
    print(f"- Model used: {chat.llm.current_model}")
    print(f"- Conversation history: {len(chat.conversation_history)} exchanges")


if __name__ == "__main__":
    test_bartholomew()
