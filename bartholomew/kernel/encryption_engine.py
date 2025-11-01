"""
Encryption Engine for Bartholomew
Implements rule-based encryption for sensitive memory content
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Tuple, Dict, Any
import base64
import json
import logging
import os
import secrets

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover
    AESGCM = None

logger = logging.getLogger(__name__)

ENC_SCHEME = "bartholomew.enc.v1"
ALG_AESGCM = "AES-GCM"  # 256-bit


def b64e(b: bytes) -> str:
    """Base64url encode bytes to string"""
    return base64.urlsafe_b64encode(b).decode("ascii")


def b64d(s: str) -> bytes:
    """Base64url decode string to bytes"""
    return base64.urlsafe_b64decode(s.encode("ascii"))


@dataclass(frozen=True)
class Envelope:
    """
    Encryption envelope with metadata for decryption
    
    Fields:
        scheme: Version identifier (bartholomew.enc.v1)
        alg: Encryption algorithm (AES-GCM)
        kid: Key identifier
        nonce: Base64url encoded nonce/IV
        aad: Optional base64url encoded additional authenticated data
        ct: Base64url encoded ciphertext (includes authentication tag for AEAD)
    """
    scheme: str
    alg: str
    kid: str
    nonce: str
    aad: Optional[str]
    ct: str

    def to_json(self) -> str:
        """Serialize envelope to JSON string"""
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=False)

    @staticmethod
    def from_json(s: str) -> Optional["Envelope"]:
        """
        Deserialize envelope from JSON string
        
        Returns None if string is not a valid envelope
        """
        try:
            obj = json.loads(s)
            if not isinstance(obj, dict):
                return None
            if obj.get("scheme") != ENC_SCHEME:
                return None
            return Envelope(
                scheme=obj["scheme"],
                alg=obj["alg"],
                kid=obj["kid"],
                nonce=obj["nonce"],
                aad=obj.get("aad"),
                ct=obj["ct"],
            )
        except Exception:
            return None


class KeyProvider:
    """Abstract key provider interface"""
    
    def get_key_by_strength(self, strength: str) -> Tuple[str, bytes]:
        """
        Get key and key ID by strength level
        
        Args:
            strength: "standard" or "strong"
            
        Returns:
            Tuple of (key_id, key_bytes)
        """
        raise NotImplementedError

    def get_key(self, kid: str) -> bytes:
        """
        Get key by key ID
        
        Args:
            kid: Key identifier
            
        Returns:
            Key bytes
        """
        raise NotImplementedError


class EnvKeyProvider(KeyProvider):
    """
    Loads keys from environment variables
    
    Environment variables:
        BME_KEY_STANDARD: urlsafe base64 encoded 32 bytes
        BME_KEY_STRONG: urlsafe base64 encoded 32 bytes
        BME_KID_STANDARD: optional key ID override (default: "std")
        BME_KID_STRONG: optional key ID override (default: "str")
    
    Falls back to ephemeral keys in development if not set (logs warning)
    """
    STANDARD_ENV = "BME_KEY_STANDARD"
    STRONG_ENV = "BME_KEY_STRONG"

    def __init__(self) -> None:
        self._cache: Dict[str, bytes] = {}
        
        # Optional explicit key IDs
        self.standard_kid = os.getenv("BME_KID_STANDARD", "std")
        self.strong_kid = os.getenv("BME_KID_STRONG", "str")

        # Load keys from environment
        std = os.getenv(self.STANDARD_ENV)
        srg = os.getenv(self.STRONG_ENV)

        if std:
            try:
                self._cache[self.standard_kid] = b64d(std)
            except Exception:
                logger.error(
                    "Invalid BME_KEY_STANDARD; must be urlsafe base64 32 bytes"
                )
        if srg:
            try:
                self._cache[self.strong_kid] = b64d(srg)
            except Exception:
                logger.error(
                    "Invalid BME_KEY_STRONG; must be urlsafe base64 32 bytes"
                )

        # Development fallback: generate ephemeral keys
        if self.standard_kid not in self._cache:
            self._cache[self.standard_kid] = secrets.token_bytes(32)
            logger.warning(
                "Using ephemeral dev key for STANDARD. "
                "Set BME_KEY_STANDARD in production."
            )
        if self.strong_kid not in self._cache:
            self._cache[self.strong_kid] = secrets.token_bytes(32)
            logger.warning(
                "Using ephemeral dev key for STRONG. "
                "Set BME_KEY_STRONG in production."
            )

    def get_key_by_strength(self, strength: str) -> Tuple[str, bytes]:
        """Get key by strength level"""
        if strength == "strong":
            return self.strong_kid, self._cache[self.strong_kid]
        # Default to standard
        return self.standard_kid, self._cache[self.standard_kid]

    def get_key(self, kid: str) -> bytes:
        """Get key by key ID"""
        return self._cache[kid]


class EncryptionStrategy:
    """Abstract encryption strategy interface"""
    
    def encrypt(self, plaintext: str, key: bytes, aad: bytes) -> Envelope:
        """
        Encrypt plaintext with key and additional authenticated data
        
        Args:
            plaintext: Text to encrypt
            key: Encryption key bytes
            aad: Additional authenticated data to bind to ciphertext
            
        Returns:
            Envelope with encrypted data (kid field not filled)
        """
        raise NotImplementedError

    def decrypt(self, envelope: Envelope, key: bytes) -> str:
        """
        Decrypt envelope with key
        
        Args:
            envelope: Encryption envelope
            key: Decryption key bytes
            
        Returns:
            Decrypted plaintext
        """
        raise NotImplementedError


class AesGcmStrategy(EncryptionStrategy):
    """
    AES-GCM 256-bit encryption strategy
    
    Uses cryptography library's AESGCM implementation with 96-bit nonces
    """
    
    def __init__(self) -> None:
        if AESGCM is None:
            raise RuntimeError(
                "cryptography package is required for AES-GCM"
            )
    
    def encrypt(self, plaintext: str, key: bytes, aad: bytes) -> Envelope:
        """Encrypt plaintext using AES-GCM"""
        aes = AESGCM(key)
        nonce = secrets.token_bytes(12)  # 96-bit nonce for GCM
        ct = aes.encrypt(nonce, plaintext.encode("utf-8"), aad)
        return Envelope(
            scheme=ENC_SCHEME,
            alg=ALG_AESGCM,
            kid="",  # Filled by engine
            nonce=b64e(nonce),
            aad=b64e(aad) if aad else None,
            ct=b64e(ct),
        )
    
    def decrypt(self, envelope: Envelope, key: bytes) -> str:
        """Decrypt envelope using AES-GCM"""
        if envelope.alg != ALG_AESGCM:
            raise ValueError(f"Unsupported algorithm: {envelope.alg}")
        aes = AESGCM(key)
        nonce = b64d(envelope.nonce)
        aad = b64d(envelope.aad) if envelope.aad else None
        ct = b64d(envelope.ct)
        pt = aes.decrypt(nonce, ct, aad)
        return pt.decode("utf-8")


class EncryptionEngine:
    """
    Orchestrates encryption based on rule policy
    
    Integrates with memory rules engine to apply encryption when
    specified by governance rules.
    """
    
    def __init__(
        self,
        key_provider: Optional[KeyProvider] = None,
        strategy: Optional[EncryptionStrategy] = None,
    ) -> None:
        """
        Initialize encryption engine
        
        Args:
            key_provider: Key provider instance (default: EnvKeyProvider)
            strategy: Encryption strategy (default: AesGcmStrategy)
        """
        self.key_provider = key_provider or EnvKeyProvider()
        self.strategy = strategy or AesGcmStrategy()

    @staticmethod
    def _decide_strength(meta: Dict[str, Any]) -> Optional[str]:
        """
        Resolve effective encryption strength from rules metadata
        
        Accepts values:
            - "strong", "standard": explicit strength
            - True: interpreted as "standard"
            - False/None: no encryption
            
        Args:
            meta: Evaluated memory metadata from rules engine
            
        Returns:
            "standard", "strong", or None
        """
        enc = meta.get("encrypt")
        if enc is True:
            return "standard"
        if isinstance(enc, str):
            enc_l = enc.lower().strip()
            if enc_l in ("standard", "strong"):
                return enc_l
            if enc_l in ("yes", "true"):
                return "standard"
        return None

    @staticmethod
    def _build_aad(context: Dict[str, Any]) -> bytes:
        """
        Build additional authenticated data from memory context
        
        Binds ciphertext to memory identifiers (kind, key, ts) to prevent
        context-less swapping of encrypted values across rows.
        
        Args:
            context: Memory context with kind, key, ts fields
            
        Returns:
            JSON-encoded AAD bytes
        """
        aad_obj = {
            "kind": context.get("kind"),
            "key": context.get("key"),
            "ts": context.get("ts"),
        }
        return json.dumps(
            aad_obj, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def encrypt_for_policy(
        self,
        plaintext: str,
        meta: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[str]:
        """
        Encrypt plaintext if required by policy
        
        Args:
            plaintext: Text to potentially encrypt
            meta: Evaluated memory metadata from rules engine
            context: Memory context (kind, key, ts) for AAD binding
            
        Returns:
            Serialized envelope JSON if encryption required, else None
        """
        strength = self._decide_strength(meta)
        if not strength:
            return None
        
        kid, key = self.key_provider.get_key_by_strength(strength)
        aad = self._build_aad(context)
        env = self.strategy.encrypt(plaintext, key, aad)
        
        # Fill kid after encryption to keep strategy generic
        env = Envelope(
            scheme=env.scheme,
            alg=env.alg,
            kid=kid,
            nonce=env.nonce,
            aad=env.aad,
            ct=env.ct,
        )
        return env.to_json()

    def try_decrypt_if_envelope(
        self, value: str, context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Best-effort decrypt if value is an envelope
        
        Args:
            value: Potentially encrypted value string
            context: Optional memory context (not used; AAD in envelope)
            
        Returns:
            Decrypted plaintext if envelope, else original value
        """
        env = Envelope.from_json(value)
        if not env:
            return value
        
        try:
            key = self.key_provider.get_key(env.kid)
            return self.strategy.decrypt(env, key)
        except Exception as e:
            logger.error(f"Failed to decrypt envelope: {e}")
            return value


# Module-level singleton for shared access
_encryption_engine = EncryptionEngine()
