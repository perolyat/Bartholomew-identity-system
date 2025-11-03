#!/usr/bin/env python3
"""
Simple chat interface for Bartholomew
Tests the full identity interpretation system with real Ollama backend
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


# Add the package to path for development
sys.path.insert(0, str(Path(__file__).parent))

from identity_interpreter import load_identity, normalize_identity
from identity_interpreter.adapters.llm_stub import LLMAdapter
from identity_interpreter.adapters.memory_manager import ConversationTurn
from identity_interpreter.adapters.metrics_logger import MetricsLogger
from identity_interpreter.adapters.storage import StorageAdapter
from identity_interpreter.policies import (
    check_red_lines,
    get_persona_config,
    handle_low_confidence,
    select_model,
)


class BartholomewChat:
    """Simple chat interface for Bartholomew AI companion"""

    def __init__(self, identity_path: str = "Identity.yaml"):
        """Initialize Bartholomew with identity configuration"""
        print("🤖 Initializing Bartholomew...")

        # Generate session ID for this conversation
        self.session_id = str(uuid.uuid4())[:8]
        print(f"📝 Session ID: {self.session_id}")

        # Load and normalize identity
        self.identity = load_identity(identity_path)
        self.identity = normalize_identity(self.identity)
        print(f"✅ Identity loaded: {self.identity.meta.name}")

        # Initialize adapters
        self.llm = LLMAdapter(self.identity)
        self.metrics = MetricsLogger(self.identity)
        self.storage = StorageAdapter(self.identity)

        # Get persona configuration
        self.persona = get_persona_config(self.identity, context="casual")
        print(f"🎭 Persona: {', '.join(self.persona['traits'])}")
        print(f"🗣️  Tone: {', '.join(self.persona['tone'])}")

        # Load previous conversation history from memory
        self.conversation_history = []
        self._load_recent_conversation_history()

    def _load_recent_conversation_history(self):
        """Load recent conversation history from persistent memory"""
        try:
            recent_turns = self.storage.memory.get_recent_conversation(limit=5)
            for turn in recent_turns:
                self.conversation_history.append(
                    {
                        "user": turn.user_input,
                        "assistant": turn.ai_response,
                        "metadata": {
                            "model": turn.model_used,
                            "confidence": turn.confidence,
                            "timestamp": turn.timestamp,
                        },
                    },
                )

            if recent_turns:
                print(f"💾 Loaded {len(recent_turns)} previous messages")
        except Exception as e:
            print(f"⚠️  Could not load conversation history: {e}")
            # Continue without history - not critical

    def get_response(self, user_input: str) -> str:
        """Generate response using the full identity system"""

        # 1. Select model based on task type
        model_decision = select_model(self.identity, task_type="general")
        print(f"🧠 Using model: {model_decision.decision['model']}")

        # 2. Check for red line violations in user input
        red_line_check = check_red_lines(self.identity, user_input)
        if red_line_check.decision["blocked"]:
            return (
                "I notice that request might violate some of my core "
                "principles. Could you rephrase that?"
            )

        # 3. Build prompt with persona and conversation history
        system_prompt = self._build_system_prompt()
        full_prompt = self._build_full_prompt(system_prompt, user_input)

        # 4. Generate response
        llm_response = self.llm.generate(
            prompt=full_prompt,
            model=model_decision.decision["model"],
            parameters=model_decision.decision["parameters"],
        )

        if not llm_response.get("success", True):
            return f"Sorry, I encountered an issue: {llm_response['response']}"

        response_text = llm_response["response"].strip()

        # 5. Check response for red lines
        response_check = check_red_lines(self.identity, response_text)
        if response_check.decision["blocked"]:
            return (
                "I generated a response that doesn't align with my "
                "values. Let me try to help you in a different way."
            )

        # 6. Handle confidence (simulate confidence score for now)
        confidence_score = 0.8  # In real system, this would come from model
        confidence_decision = handle_low_confidence(
            self.identity,
            confidence_score,
        )

        if confidence_decision.decision["is_low_confidence"]:
            note = (
                "\n\n(Note: I'm not entirely confident about this "
                "response. Please verify if this is important.)"
            )
            response_text += note

        # 7. Log metrics and store conversation
        self.metrics.log_decision(
            "chat_response",
            {
                "model_used": model_decision.decision["model"],
                "tokens_used": llm_response.get("tokens_used", 0),
                "confidence": confidence_score,
            },
            model_decision.rationale,
        )

        # 8. Store in persistent memory
        conversation_turn = ConversationTurn(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            user_input=user_input,
            ai_response=response_text,
            context={
                "session_id": getattr(self, "session_id", "unknown"),
                "model_decision": model_decision.decision,
                "confidence_decision": confidence_decision.decision,
            },
            confidence=confidence_score,
            model_used=model_decision.decision["model"],
        )

        # Store in memory system
        self.storage.memory.store_conversation_turn(conversation_turn)

        # Store in conversation history (backwards compatibility)
        self.conversation_history.append(
            {
                "user": user_input,
                "assistant": response_text,
                "metadata": {
                    "model": model_decision.decision["model"],
                    "confidence": confidence_score,
                },
            },
        )

        return response_text

    def _build_system_prompt(self) -> str:
        """Build system prompt based on identity configuration"""
        values = self.identity.values_and_principles.core_values
        traits = self.persona["traits"]
        tone = self.persona["tone"]

        return f"""You are {self.identity.meta.name}, an AI companion.

Core values: {', '.join(values)}
Personality traits: {', '.join(traits)}
Communication tone: {', '.join(tone)}

Guidelines:
- Be helpful, honest, and transparent about your AI nature
- Prioritize user autonomy and well-being
- Admit uncertainty when you're not confident
- Be concise but expand if asked
- Show trade-offs rather than manipulate

Remember: You are {self.identity.meta.description}"""

    def _build_full_prompt(self, system_prompt: str, user_input: str) -> str:
        """Build full prompt with conversation history"""
        prompt_parts = [system_prompt]

        # Add recent conversation history (last 3 exchanges)
        recent_history = self.conversation_history[-3:]
        for exchange in recent_history:
            prompt_parts.append(f"Human: {exchange['user']}")
            prompt_parts.append(f"Assistant: {exchange['assistant']}")

        # Add current user input
        prompt_parts.append(f"Human: {user_input}")
        prompt_parts.append("Assistant:")

        return "\n\n".join(prompt_parts)

    def chat_loop(self):
        """Main chat loop"""
        print("\n" + "=" * 60)
        print(f"🤖 {self.identity.meta.name} is ready to chat!")
        print("Type 'quit' to exit, 'help' for commands")
        print("=" * 60 + "\n")

        while True:
            try:
                user_input = input("You: ").strip()

                if user_input.lower() in ["quit", "exit", "bye"]:
                    print("\n👋 Goodbye! It was nice chatting with you.")
                    break
                elif user_input.lower() == "help":
                    self._show_help()
                    continue
                elif user_input.lower() == "status":
                    self._show_status()
                    continue
                elif not user_input:
                    continue

                # Get and display response
                response = self.get_response(user_input)
                print(f"\n{self.identity.meta.name}: {response}\n")

            except KeyboardInterrupt:
                print("\n\n👋 Goodbye! Use 'quit' next time for a cleaner exit.")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                print("Please try again or type 'quit' to exit.")

    def _show_help(self):
        """Show available commands"""
        print(
            f"""
🤖 {self.identity.meta.name} Commands:
- help: Show this help
- status: Show system status
- quit/exit/bye: End conversation
""",
        )

    def _show_status(self):
        """Show system status"""
        print(
            f"""
📊 System Status:
- Identity: {self.identity.meta.name} v{self.identity.meta.version}
- Current model: {self.llm.current_model or 'Not yet used'}
- Conversation exchanges: {len(self.conversation_history)}
- Red lines active: {len(self.identity.red_lines)}
- Safety features: {'✅' if self.identity.safety_and_alignment.controls.kill_switch.enabled else '❌'}
""",
        )


def main():
    """Entry point for chat interface"""
    try:
        chat = BartholomewChat()
        chat.chat_loop()
    except FileNotFoundError:
        print("❌ Error: Identity.yaml not found. Make sure you're in the right directory.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
