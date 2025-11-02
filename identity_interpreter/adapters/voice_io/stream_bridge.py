"""
Voice stream bridge stub (for parking brake gating).
"""


def start_stream(db_path: str = None) -> None:
    """
    Start voice stream processing.
    
    Args:
        db_path: Optional database path for brake check
    
    Returns:
        None (early return if blocked)
    """
    # Parking brake gate for voice scope
    try:
        from bartholomew.orchestrator.safety.parking_brake import (
            ParkingBrake, BrakeStorage
        )
        from bartholomew.kernel.daemon import _default_db_path
        
        path = db_path or _default_db_path()
        storage = BrakeStorage(path)
        brake = ParkingBrake(storage)
        if brake.is_blocked("voice"):
            return  # Blocked, return early
    except ImportError:
        # Parking brake module not available, continue normally
        pass
    
    # Normal streaming would happen here
    print("Voice stream started")
