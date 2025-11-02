"""
Sight pipeline stub (for parking brake gating).
"""
from typing import Dict, Any


def start_capture(db_path: str = None) -> Dict[str, Any]:
    """
    Start visual capture pipeline.
    
    Args:
        db_path: Optional database path for brake check
    
    Returns:
        Dict with capture status or blocked flag
    """
    # Parking brake gate for sight scope
    try:
        from bartholomew.orchestrator.safety.parking_brake import (
            ParkingBrake, BrakeStorage
        )
        from bartholomew.kernel.daemon import _default_db_path
        
        path = db_path or _default_db_path()
        storage = BrakeStorage(path)
        brake = ParkingBrake(storage)
        if brake.is_blocked("sight"):
            return {"blocked": True}
    except ImportError:
        # Parking brake module not available, continue normally
        pass
    
    # Normal capture would happen here
    return {"blocked": False, "status": "capturing"}
