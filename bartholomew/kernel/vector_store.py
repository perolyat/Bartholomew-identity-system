"""
Vector Store for Bartholomew
Implements SQLite-backed vector storage with optional sqlite-vss acceleration
"""
from __future__ import annotations
import logging
import sqlite3
from typing import List, Optional, Tuple

import numpy as np

from bartholomew.kernel.db_ctx import set_wal_pragmas

logger = logging.getLogger(__name__)


# Schema for vector embeddings table
VECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_embeddings (
  embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id    INTEGER NOT NULL,
  source       TEXT NOT NULL CHECK(source IN ('summary','full')),
  dim          INTEGER NOT NULL,
  vec          BLOB NOT NULL,
  norm         REAL NOT NULL,
  provider     TEXT NOT NULL,
  model        TEXT NOT NULL,
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mememb_memory_id
  ON memory_embeddings(memory_id);
CREATE INDEX IF NOT EXISTS idx_mememb_source
  ON memory_embeddings(source);
CREATE INDEX IF NOT EXISTS idx_mememb_dim
  ON memory_embeddings(dim);
"""


class VectorStore:
    """
    SQLite-backed vector storage with fallback search strategies
    
    Attempts to use sqlite-vss for ANN search if available.
    Falls back to brute-force NumPy cosine similarity otherwise.
    """
    
    def __init__(self, db_path: str) -> None:
        """
        Initialize vector store
        
        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.vss_available = False
        self.vss_dim = 384  # Phase 2d+: VSS hardcoded to 384
        
        # Check VSS first, then initialize schema
        self._check_vss_availability()
        self._init_schema()
    
    def _init_schema(self) -> None:
        """Create vector embeddings table if not exists"""
        with sqlite3.connect(self.db_path) as conn:
            set_wal_pragmas(conn)
            conn.executescript(VECTOR_SCHEMA)
            conn.commit()
            
            # Phase 2d+: Create VSS triggers if extension available
            if self.vss_available:
                self._create_vss_triggers(conn)
    
    def _check_vss_availability(self) -> None:
        """
        Check if sqlite-vss extension is available
        
        Attempts to load the extension. If successful, sets flag.
        This is optional; we fall back to brute-force if unavailable.
        
        Phase 2d+: Disable VSS if configured dim != 384 (VSS hardcoded)
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                set_wal_pragmas(conn)
                # Try to load vss extension
                conn.enable_load_extension(True)
                conn.load_extension("vss0")
                
                # Phase 2d+: Check if current embedding dim matches VSS
                current_dim = self._get_current_dim()
                if current_dim != self.vss_dim:
                    logger.error(
                        f"VSS disabled: dim mismatch (config {current_dim} "
                        f"!= {self.vss_dim}). Using brute-force. "
                        "Run 'bartholomew admin embeddings rebuild-vss' "
                        "after changing model/dim."
                    )
                    self.vss_available = False
                else:
                    self.vss_available = True
                    logger.info("sqlite-vss extension loaded successfully")
        except Exception as e:
            logger.info(
                f"sqlite-vss not available ({e}), "
                "using brute-force cosine fallback"
            )
            self.vss_available = False
    
    def _get_current_dim(self) -> int:
        """
        Get current embedding dimension from config
        
        Returns:
            Configured embedding dimension (default 384)
        """
        try:
            import os
            import yaml
            
            # Try to load embeddings.yaml
            for path in [
                os.path.join("bartholomew", "config", "embeddings.yaml"),
                os.path.join("config", "embeddings.yaml"),
            ]:
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        data = yaml.safe_load(f) or {}
                    emb = data.get("embeddings", {})
                    return emb.get("default_dim", 384)
        except Exception:
            pass
        
        # Default to 384
        return 384
    
    def _create_vss_triggers(self, conn: sqlite3.Connection) -> None:
        """
        Create VSS virtual table and triggers for automatic mirroring
        
        Phase 2d+: DB triggers keep VSS table in sync with no Python logic
        """
        try:
            # Create VSS virtual table for 384-dim vectors
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings_vss
                USING vss0(vec(384))
            """)
            
            # Trigger: AFTER INSERT (only for dim=384)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_mememb_insert
                AFTER INSERT ON memory_embeddings
                WHEN NEW.dim = 384
                BEGIN
                    INSERT INTO memory_embeddings_vss(rowid, vec)
                    VALUES (NEW.embedding_id, NEW.vec);
                END
            """)
            
            # Trigger: AFTER UPDATE (handle dim changes)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_mememb_update
                AFTER UPDATE OF vec, dim, model, provider, source
                ON memory_embeddings
                BEGIN
                    DELETE FROM memory_embeddings_vss
                    WHERE rowid = NEW.embedding_id;
                    
                    INSERT INTO memory_embeddings_vss(rowid, vec)
                    SELECT NEW.embedding_id, NEW.vec
                    WHERE NEW.dim = 384;
                END
            """)
            
            # Trigger: AFTER DELETE
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_mememb_delete
                AFTER DELETE ON memory_embeddings
                BEGIN
                    DELETE FROM memory_embeddings_vss
                    WHERE rowid = OLD.embedding_id;
                END
            """)
            
            conn.commit()
            logger.info("Created VSS triggers for automatic mirroring")
        except Exception as e:
            logger.warning(f"Failed to create VSS triggers: {e}")
            self.vss_available = False
    
    def upsert(
        self,
        memory_id: int,
        vec: np.ndarray,
        source: str,
        provider: str,
        model: str
    ) -> None:
        """
        Insert or update an embedding
        
        Args:
            memory_id: ID of the memory this embedding belongs to
            vec: Embedding vector (1D numpy array, float32, normalized)
            source: 'summary' or 'full'
            provider: Provider name (e.g., 'local-sbert')
            model: Model identifier
        """
        # Validate inputs
        if vec.ndim != 1:
            raise ValueError(
                f"Expected 1D vector, got shape {vec.shape}"
            )
        
        if vec.dtype != np.float32:
            vec = vec.astype(np.float32)
        
        if source not in ('summary', 'full'):
            raise ValueError(
                f"source must be 'summary' or 'full', got {source}"
            )
        
        # Compute norm
        norm = float(np.linalg.norm(vec))
        
        # Encode vector as BLOB
        vec_blob = vec.tobytes()
        dim = len(vec)
        
        with sqlite3.connect(self.db_path) as conn:
            set_wal_pragmas(conn)
            # Check if embedding already exists for this memory/source
            cursor = conn.execute(
                "SELECT embedding_id FROM memory_embeddings "
                "WHERE memory_id=? AND source=?",
                (memory_id, source)
            )
            existing = cursor.fetchone()
            
            if existing:
                # Update existing
                conn.execute(
                    "UPDATE memory_embeddings SET "
                    "vec=?, norm=?, dim=?, provider=?, model=?, "
                    "created_at=CURRENT_TIMESTAMP "
                    "WHERE embedding_id=?",
                    (vec_blob, norm, dim, provider, model, existing[0])
                )
            else:
                # Insert new
                conn.execute(
                    "INSERT INTO memory_embeddings "
                    "(memory_id, source, dim, vec, norm, provider, model) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (memory_id, source, dim, vec_blob, norm, provider, model)
                )
            
            conn.commit()
    
    def delete_for_memory(self, memory_id: int) -> None:
        """
        Delete all embeddings for a memory
        
        Args:
            memory_id: Memory ID to delete embeddings for
        """
        with sqlite3.connect(self.db_path) as conn:
            set_wal_pragmas(conn)
            conn.execute(
                "DELETE FROM memory_embeddings WHERE memory_id=?",
                (memory_id,)
            )
            conn.commit()
    
    def search(
        self,
        qvec: np.ndarray,
        top_k: int = 8,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        dim: Optional[str] = None,
        source: Optional[str] = None,
        allow_mismatch: bool = False,
        apply_consent_gate: bool = True
    ) -> List[Tuple[int, float]]:
        """
        Search for similar embeddings
        
        Privacy gates are applied by default to exclude:
        - never_store memories (allow_store=false)
        - ask_before_store memories without consent
        
        Args:
            qvec: Query vector (1D numpy array, float32, normalized)
            top_k: Number of results to return
            provider: Optional filter by provider (strict by default)
            model: Optional filter by model (strict by default)
            dim: Optional filter by dimension (strict by default)
            source: Optional filter by source ('summary' or 'full')
            allow_mismatch: If False (default), only return vectors
                          matching provider/model/dim when specified.
                          Backward compat: treated as True when all
                          provider/model/dim are None.
            apply_consent_gate: If True (default), apply privacy filtering
            
        Returns:
            List of (memory_id, score) tuples, sorted by score descending
            Score is cosine similarity (0-1, higher is better)
        """
        if qvec.ndim != 1:
            raise ValueError(
                f"Expected 1D query vector, got shape {qvec.shape}"
            )
        
        if qvec.dtype != np.float32:
            qvec = qvec.astype(np.float32)
        
        # Normalize query vector
        qnorm = np.linalg.norm(qvec)
        if qnorm > 0:
            qvec = qvec / qnorm
        
        # Backward compat: if no filters specified, allow mismatch
        if provider is None and model is None and dim is None:
            allow_mismatch = True
        
        # Fetch more candidates if consent filtering is enabled
        fetch_k = top_k * 3 if apply_consent_gate else top_k
        
        if self.vss_available:
            results = self._search_vss(
                qvec, fetch_k, provider, model, dim, source, allow_mismatch
            )
        else:
            results = self._search_bruteforce(
                qvec, fetch_k, provider, model, dim, source, allow_mismatch
            )
        
        # Apply consent gate if enabled
        if apply_consent_gate and results:
            from bartholomew.kernel.consent_gate import ConsentGate
            gate = ConsentGate(self.db_path)
            results = gate.apply_to_vector_results(results)
            
            # Trim to requested top_k after filtering
            results = results[:top_k]
        
        return results
    
    def _search_vss(
        self,
        qvec: np.ndarray,
        top_k: int,
        provider: Optional[str],
        model: Optional[str],
        dim: Optional[int],
        source: Optional[str],
        allow_mismatch: bool
    ) -> List[Tuple[int, float]]:
        """
        Search using sqlite-vss (if available)
        
        For Phase 2d, this is a placeholder. Full VSS integration
        requires creating a virtual table and maintaining sync.
        Fall back to brute-force for now.
        """
        logger.warning(
            "VSS search not fully implemented, using brute-force"
        )
        return self._search_bruteforce(
            qvec, top_k, provider, model, dim, source, allow_mismatch
        )
    
    def _search_bruteforce(
        self,
        qvec: np.ndarray,
        top_k: int,
        provider: Optional[str],
        model: Optional[str],
        dim: Optional[int],
        source: Optional[str],
        allow_mismatch: bool
    ) -> List[Tuple[int, float]]:
        """
        Brute-force cosine similarity search
        
        Loads all vectors, computes dot products, returns top-k.
        Efficient enough for small to medium datasets (<10k vectors).
        """
        with sqlite3.connect(self.db_path) as conn:
            set_wal_pragmas(conn)
            # Build query with optional filters
            query = (
                "SELECT memory_id, vec, dim, provider, model "
                "FROM memory_embeddings WHERE 1=1"
            )
            params: List = []
            
            # Phase 2d+: Strict model matching (unless allow_mismatch)
            if not allow_mismatch:
                if provider is not None:
                    query += " AND provider=?"
                    params.append(provider)
                if model is not None:
                    query += " AND model=?"
                    params.append(model)
                if dim is not None:
                    query += " AND dim=?"
                    params.append(dim)
            else:
                # Backward compat: only filter by dim if specified
                if dim is not None:
                    query += " AND dim=?"
                    params.append(dim)
            
            if source is not None:
                query += " AND source=?"
                params.append(source)
            
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
        
        if not rows:
            return []
        
        # Decode vectors and compute scores
        results: List[Tuple[int, float]] = []
        q_dim = len(qvec)
        
        for row in rows:
            memory_id, vec_blob, vec_dim = row[0], row[1], row[2]
            # provider, model unused but available at row[3], row[4]
            
            # Skip if dimension mismatch
            if vec_dim != q_dim:
                continue
            
            # Decode vector from BLOB
            vec = np.frombuffer(vec_blob, dtype=np.float32)
            
            # Compute cosine similarity (dot product of normalized vectors)
            score = float(np.dot(qvec, vec))
            
            # Clamp to [0, 1] to handle numerical errors
            score = max(0.0, min(1.0, score))
            
            results.append((memory_id, score))
        
        # Sort by score descending and take top-k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
    
    def count(self) -> int:
        """
        Count total number of embeddings
        
        Returns:
            Number of embedding rows
        """
        with sqlite3.connect(self.db_path) as conn:
            set_wal_pragmas(conn)
            cursor = conn.execute(
                "SELECT COUNT(*) FROM memory_embeddings"
            )
            row = cursor.fetchone()
            return row[0] if row else 0


# Module-level singleton (optional)
_vector_store: Optional[VectorStore] = None


def get_vector_store(db_path: str) -> VectorStore:
    """
    Get or create vector store singleton for a database path
    
    Args:
        db_path: Database file path
        
    Returns:
        VectorStore instance
    """
    global _vector_store
    if _vector_store is None or _vector_store.db_path != db_path:
        _vector_store = VectorStore(db_path)
    return _vector_store
