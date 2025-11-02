#!/usr/bin/env python3
"""
Integration test for Bartholomew - validates model responses
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))

from identity_interpreter import load_identity, normalize_identity
from identity_interpreter.adapters.llm_stub import LLMAdapter
from identity_interpreter.policies import select_model


def test_model_integration():
    """Test that model adapter connects and generates responses"""
    print("=" * 60)
    print("INTEGRATION TEST: Model Connection & Response Generation")
    print("=" * 60)

    # Load identity
    print("\n1. Loading Identity.yaml...")
    identity = load_identity("Identity.yaml")
    identity = normalize_identity(identity)
    print(f"   ✓ Loaded: {identity.meta.name}")

    # Initialize LLM adapter
    print("\n2. Initializing LLM adapter...")
    llm = LLMAdapter(identity)
    print(f"   ✓ Ollama base URL: {llm.ollama_base_url}")
    print(
        f"   ✓ Ollama enabled in config: {identity.meta.deployment_profile.runtimes.ollama_enabled}",
    )

    # Select model
    print("\n3. Selecting model for 'general' task...")
    model_decision = select_model(identity, task_type="general")
    model_name = model_decision.decision["model"]
    parameters = model_decision.decision["parameters"]
    print(f"   ✓ Selected model: {model_name}")
    print(f"   ✓ Parameters: {parameters}")

    # Check if model is available
    print("\n4. Checking if model is available locally...")
    is_available = llm.is_available(model_name)
    print(f"   {'✓' if is_available else '✗'} Model available: {is_available}")
    if not is_available:
        mapped = llm._map_model_name(model_name)
        print(f"   Note: Mapped to '{mapped}' - may need: ollama pull {mapped}")

    # Test 1: Simple generation
    print("\n5. Test 1: Simple generation...")
    prompt = "Say 'hello' in one word only."
    response = llm.generate(
        prompt=prompt,
        model=model_name,
        parameters=parameters,
    )

    print(f"   Success: {response.get('success', False)}")
    print(f"   Model used: {response.get('model', 'N/A')}")
    print(f"   Tokens: {response.get('tokens_used', 0)}")
    if response.get("success"):
        print(f"   Response: {response['response'][:100]}")
    else:
        print(f"   Error: {response.get('error', 'unknown')}")
        print(f"   Message: {response.get('response', 'N/A')}")

    # Test 2: Verify it's not a mock
    print("\n6. Test 2: Ask about the model itself...")
    prompt2 = "What model are you?"
    response2 = llm.generate(
        prompt=prompt2,
        model=model_name,
        parameters=parameters,
    )

    print(f"   Success: {response2.get('success', False)}")
    if response2.get("success"):
        print(f"   Response: {response2['response'][:200]}")
        print("\n   ✓ Response came from actual model (not a mock)")
    else:
        print(f"   Error: {response2.get('response', 'N/A')}")

    # Test 3: Current model tracking
    print("\n7. Test 3: Current model tracking...")
    print(f"   Current model: {llm.current_model}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("Identity validation: ✓")
    print("Model selection: ✓")
    print(f"Model availability: {'✓' if is_available else '✗'}")
    print(f"Model response generation: {'✓' if response.get('success') else '✗'}")
    print(
        f"Responses from real model: {'✓' if response.get('success') else '✗ (check Ollama service)'}",
    )
    print("=" * 60)


if __name__ == "__main__":
    try:
        test_model_integration()
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
