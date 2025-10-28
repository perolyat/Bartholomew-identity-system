"""
System Health
-------------
Quick health checks for memory and orchestration subsystems.
"""
from pathlib import Path


def health_check() -> None:
    """Run system health checks and print report."""
    print("🧠 Memory Subsystem Health Report:")
    print("-" * 50)
    
    # Memory health (will be skipped if no identity config available)
    try:
        from identity_interpreter.loader import load_identity
        identity = load_identity("Identity.yaml")
        from identity_interpreter.adapters.memory_manager import (
            MemoryManager
        )
        memory = MemoryManager(identity)
        result = memory.health_check()
        for k, v in result.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"  status: unavailable ({e})")
    
    print("\n⚙️  Orchestrator Subsystem Health Report:")
    print("-" * 50)
    
    # Check log directory
    log_dir = Path("logs/orchestrator")
    if log_dir.exists():
        print(f"  log_directory: {log_dir} (exists)")
        if log_dir.is_dir():
            print("  log_directory_writable: checking...")
            try:
                test_file = log_dir / ".health_check"
                test_file.touch()
                test_file.unlink()
                print("  log_directory_writable: True")
            except Exception as e:
                print(f"  log_directory_writable: False ({e})")
        else:
            print("  log_directory_writable: False (not a directory)")
    else:
        print(f"  log_directory: {log_dir} (will be created on first use)")
    
    # Check contract
    contract_path = Path("identity_interpreter/contracts/orchestration.yaml")
    if contract_path.exists():
        print(f"  contract_file: {contract_path} (exists)")
    else:
        print(f"  contract_file: {contract_path} (not found)")
    
    print("\n✅ Health check complete")
