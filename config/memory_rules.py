import yaml
import re
from pathlib import Path

class MemoryRulesEngine:
    def __init__(self, config_path="bartholomew/config/memory_rules.yaml"):
        self.rules = self._load_rules(config_path)

    def _load_rules(self, path):
        if not Path(path).exists():
            print("[Bartholomew] No memory rules config found — allowing all.")
            return {}
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def evaluate(self, memory):
        enriched = memory.copy()
        for tier, rule_list in self.rules.items():
            for rule in rule_list:
                if self._match(rule["match"], memory):
                    metadata = rule.get("metadata", {})
                    enriched.update(metadata)
                    enriched["tier"] = tier
                    return enriched
        return enriched

    def should_store(self, memory):
        m = self.evaluate(memory)
        return m.get("allow_store", True)

    def requires_consent(self, memory):
        m = self.evaluate(memory)
        return m.get("requires_consent", False)

    def _match(self, rule, memory):
        for k, v in rule.items():
            if k == "content":
                if not re.search(v, memory.get("value", ""), re.IGNORECASE):
                    return False
            elif k == "tags":
                if not set(v).intersection(set(memory.get("tags", []))):
                    return False
            elif memory.get(k) != v:
                return False
        return True
