"""
Model Router
------------
Routes requests to appropriate LLM backends based on configuration.
"""
from typing import Dict, Any, Optional


class ModelRouter:
    """Routes requests to appropriate LLM backends."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        identity_config: Optional[Any] = None
    ):
        """
        Initialize the model router.
        
        Args:
            config: Optional configuration dictionary
            identity_config: Optional identity config for LLM adapter
        """
        self.config = config or self._default_config()
        self.identity_config = identity_config
        self.llm_adapter = None
        
        # Lazily initialize LLM adapter if identity config provided
        if identity_config:
            try:
                from ..adapters.llm_stub import LLMAdapter
                self.llm_adapter = LLMAdapter(identity_config)
            except Exception:
                # Adapter unavailable, will use stub
                pass

    def _default_config(self) -> Dict[str, Any]:
        """Return default routing configuration."""
        return {
            "default_backend": "stub",
            "default_model": "stub-llm",
            "backends": {
                "stub": {"model": "stub-llm", "temperature": 0.7},
                "openai": {"model": "gpt-4o-mini", "temperature": 0.2},
                "anthropic": {"model": "sonnet", "temperature": 0.3},
                "local": {"model": "mistral-medium", "temperature": 0.5}
            }
        }

    def select_route(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Select the appropriate routing configuration.
        
        Args:
            data: Request data containing routing hints
            
        Returns:
            Route configuration with backend, model, and parameters
        """
        backend = data.get("backend", self.config["default_backend"])
        backend_config = self.config["backends"].get(backend, {})
        
        return {
            "backend": backend,
            "model": backend_config.get(
                "model",
                self.config["default_model"]
            ),
            "parameters": {
                "temperature": backend_config.get("temperature", 0.7)
            }
        }

    def route(self, data: Dict[str, Any]) -> str:
        """
        Execute routing and return LLM response.
        
        Args:
            data: Request data with prompt and routing information
            
        Returns:
            LLM response string
        """
        route = self.select_route(data)
        prompt = data.get("prompt", data.get("user_input", ""))
        backend = route["backend"]
        
        # Use LLM adapter for local/ollama backends
        if backend in ["local", "ollama"] and self.llm_adapter:
            try:
                result = self.llm_adapter.generate(
                    prompt=prompt,
                    model=route["model"],
                    parameters=route["parameters"],
                    context=data,
                )
                if result.get("success"):
                    return result.get("response", "")
                # Fallback to stub on error
            except Exception:
                pass
        
        # Stub response for testing/fallback
        if backend == "stub":
            return (
                f"[{route['model']}] "
                f"Mock response for prompt: {prompt[:50]}..."
            )
        
        # Placeholder for other backends
        return f"[{route['model']}] Mock response for: {prompt[:80]}..."
