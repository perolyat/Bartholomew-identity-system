"""
LLM Adapter for Ollama
Provides interface for local model inference via Ollama
"""

import os
from typing import Any

import requests


# Try to import official ollama client
try:
    from ollama import Client as OllamaClient

    HAS_OLLAMA_CLIENT = True
except ImportError:
    HAS_OLLAMA_CLIENT = False


class LLMAdapter:
    """Ollama adapter for local LLM inference"""

    def __init__(self, identity_config: Any):
        """
        Initialize LLM adapter

        Args:
            identity_config: Identity configuration object
        """
        self.identity = identity_config
        self.current_model = None
        self.ollama_base_url = os.getenv(
            "OLLAMA_HOST",
            "http://localhost:11434",
        )

        # Initialize ollama client if available
        self.client = None
        if HAS_OLLAMA_CLIENT:
            try:
                self.client = OllamaClient(host=self.ollama_base_url)
            except Exception:
                # Fall back to requests-based approach
                pass

        # Model name mapping from Identity.yaml to Ollama
        self.model_mapping = {
            "Mistral-7B-Instruct-GGUF-Q4_K_M": "mistral:7b-instruct",
            "TinyLlama 1.1B": "tinyllama",
            "Phi-4 3B": "phi3:mini",
            # Additional available models
            "Qwen2.5-Coder-7B": "qwen2.5-coder:7b",
            "Gemma3": "gemma3:latest",
        }

    def _map_model_name(self, model_name: str) -> str:
        """Map Identity.yaml model names to Ollama model names"""
        return self.model_mapping.get(model_name, model_name)

    def _model_exists(self, ollama_model: str) -> bool:
        """
        Check if model exists in local Ollama

        Args:
            ollama_model: Ollama model name

        Returns:
            True if model exists locally
        """
        # Try using ollama client first
        if self.client:
            try:
                self.client.show(ollama_model)
                return True
            except Exception:
                return False

        # Fallback to REST API
        try:
            response = requests.get(
                f"{self.ollama_base_url}/api/tags",
                timeout=5,
            )
            response.raise_for_status()
            models = response.json().get("models", [])
            return any(
                m.get("name") == ollama_model or m.get("name", "").startswith(f"{ollama_model}:")
                for m in models
            )
        except Exception:
            return False

    def generate(
        self,
        prompt: str,
        model: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Generate response from LLM via Ollama

        Args:
            prompt: Input prompt
            model: Model name (from Identity.yaml)
            parameters: Model parameters (temperature, etc.)
            context: Optional context

        Returns:
            Dict with 'response', 'tokens_used', 'model', 'success'
        """
        # Check if Ollama is enabled
        runtimes = self.identity.meta.deployment_profile.runtimes
        if not runtimes.ollama_enabled:
            return {
                "response": "[ERROR] Ollama is disabled in Identity.yaml",
                "tokens_used": 0,
                "model": model,
                "parameters": parameters,
                "success": False,
                "error": "ollama_disabled",
            }

        # Validate prompt
        if not prompt or not prompt.strip():
            return {
                "response": "[ERROR] Empty prompt provided",
                "tokens_used": 0,
                "model": model,
                "parameters": parameters,
                "success": False,
                "error": "empty_prompt",
            }

        # Map and check model
        ollama_model = self._map_model_name(model)
        self.current_model = ollama_model

        if not self._model_exists(ollama_model):
            error_msg = f"[ERROR] Model '{ollama_model}' not found. Run: ollama pull {ollama_model}"
            return {
                "response": error_msg,
                "tokens_used": 0,
                "model": ollama_model,
                "parameters": parameters,
                "success": False,
                "error": "model_not_available",
            }

        # Prepare generation parameters
        options = {
            "temperature": parameters.get("temperature", 0.2),
            "top_p": parameters.get("top_p", 0.9),
            "num_predict": parameters.get("max_tokens", 1536),
        }

        try:
            # Try using ollama client first
            if self.client:
                response = self.client.chat(
                    model=ollama_model,
                    messages=[{"role": "user", "content": prompt}],
                    options=options,
                )
                text = response.get("message", {}).get("content", "")
                tokens = response.get("eval_count", 0)

                return {
                    "response": text,
                    "tokens_used": tokens,
                    "model": ollama_model,
                    "parameters": parameters,
                    "success": True,
                }

            # Fallback to REST API
            payload = {
                "model": ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": options,
            }

            response = requests.post(
                f"{self.ollama_base_url}/api/generate",
                json=payload,
                timeout=60,
            )
            response.raise_for_status()

            result = response.json()

            return {
                "response": result.get("response", ""),
                "tokens_used": result.get("eval_count", 0),
                "model": ollama_model,
                "parameters": parameters,
                "success": True,
            }

        except requests.exceptions.ConnectionError:
            error_msg = f"[ERROR] Could not connect to Ollama at {self.ollama_base_url}"
            return {
                "response": error_msg,
                "tokens_used": 0,
                "model": ollama_model,
                "parameters": parameters,
                "success": False,
                "error": "connection_failed",
            }
        except requests.exceptions.Timeout:
            error_msg = f"[ERROR] Request timed out for model {ollama_model}"
            return {
                "response": error_msg,
                "tokens_used": 0,
                "model": ollama_model,
                "parameters": parameters,
                "success": False,
                "error": "timeout",
            }
        except Exception as e:
            return {
                "response": f"[ERROR] {str(e)}",
                "tokens_used": 0,
                "model": ollama_model,
                "parameters": parameters,
                "success": False,
                "error": str(e),
            }

    def is_available(self, model: str) -> bool:
        """
        Check if model is available

        Args:
            model: Model name (from Identity.yaml)

        Returns:
            True if available
        """
        ollama_model = self._map_model_name(model)
        return self._model_exists(ollama_model)

    def get_context_window(self, model: str) -> int:
        """
        Get model's context window size

        Args:
            model: Model name

        Returns:
            Context window size in tokens
        """
        # Stub - returns default
        return 8192
