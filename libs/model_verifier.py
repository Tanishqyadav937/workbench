"""A13: Model weight verification with signed manifest and SHA-256 checksums."""

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import hmac


@dataclass
class ModelManifest:
    """Model manifest with checksums and metadata."""
    model_name: str
    version: str
    file_path: str
    file_size_bytes: int
    sha256_hash: str
    timestamp: str
    verified: bool = False
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None


class ModelVerifier:
    """
    Verifies model weights haven't been tampered with:
    - SHA-256 checksum validation
    - Signed manifest verification
    - Manifest persistence
    """
    
    def __init__(self, manifest_dir: Path = Path("/var/lib/model-manifests")):
        self.manifest_dir = Path(manifest_dir)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
    
    def compute_hash(self, file_path: str) -> str:
        """Compute SHA256 hash of a model file."""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def create_manifest(
        self,
        model_name: str,
        version: str,
        file_path: str,
    ) -> ModelManifest:
        """
        Create a manifest for a model.
        
        Args:
            model_name: Name of the model
            version: Version string
            file_path: Path to the model file
            
        Returns:
            ModelManifest object
        """
        file_obj = Path(file_path)
        if not file_obj.exists():
            raise FileNotFoundError(f"Model file not found: {file_path}")
        
        sha256_hash = self.compute_hash(file_path)
        file_size = file_obj.stat().st_size
        
        manifest = ModelManifest(
            model_name=model_name,
            version=version,
            file_path=str(file_path),
            file_size_bytes=file_size,
            sha256_hash=sha256_hash,
            timestamp=datetime.utcnow().isoformat(),
        )
        
        return manifest
    
    def save_manifest(
        self,
        manifest: ModelManifest,
        secret_key: Optional[str] = None,
    ) -> str:
        """
        Save manifest to file with optional HMAC signature.
        
        Args:
            manifest: ModelManifest to save
            secret_key: Optional secret key for HMAC signing
            
        Returns:
            Path where manifest was saved
        """
        manifest_file = self.manifest_dir / f"{manifest.model_name}-{manifest.version}.json"
        
        manifest_dict = {
            "model_name": manifest.model_name,
            "version": manifest.version,
            "file_path": manifest.file_path,
            "file_size_bytes": manifest.file_size_bytes,
            "sha256_hash": manifest.sha256_hash,
            "timestamp": manifest.timestamp,
            "verified": manifest.verified,
            "verified_by": manifest.verified_by,
            "verified_at": manifest.verified_at,
        }
        
        # Add HMAC signature if key provided
        if secret_key:
            signature = hmac.new(
                secret_key.encode(),
                json.dumps(manifest_dict, sort_keys=True).encode(),
                hashlib.sha256
            ).hexdigest()
            manifest_dict["_signature"] = signature
        
        with open(manifest_file, 'w') as f:
            json.dump(manifest_dict, f, indent=2)
        
        return str(manifest_file)
    
    def verify_model(
        self,
        model_name: str,
        version: str,
        file_path: str,
        secret_key: Optional[str] = None,
        verifier_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Verify a model hasn't been tampered with.
        
        Args:
            model_name: Name of the model
            version: Version string
            file_path: Path to model file to verify
            secret_key: Secret key for HMAC verification
            verifier_id: ID of the verifier component
            
        Returns:
            (is_valid, message)
        """
        manifest_file = self.manifest_dir / f"{model_name}-{version}.json"
        
        if not manifest_file.exists():
            return False, f"No manifest found for {model_name}:{version}"
        
        # Load manifest
        with open(manifest_file, 'r') as f:
            manifest_data = json.load(f)
        
        # Verify HMAC signature if present
        if "_signature" in manifest_data and secret_key:
            stored_sig = manifest_data.pop("_signature")
            computed_sig = hmac.new(
                secret_key.encode(),
                json.dumps(manifest_data, sort_keys=True).encode(),
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(stored_sig, computed_sig):
                return False, "Manifest signature verification failed"
        
        # Verify file hash
        if not Path(file_path).exists():
            return False, f"Model file not found: {file_path}"
        
        current_hash = self.compute_hash(file_path)
        expected_hash = manifest_data.get("sha256_hash")
        
        if current_hash != expected_hash:
            return False, (
                f"Hash mismatch for {model_name}:{version}\n"
                f"Expected: {expected_hash}\n"
                f"Got:      {current_hash}"
            )
        
        # Verify file size
        current_size = Path(file_path).stat().st_size
        expected_size = manifest_data.get("file_size_bytes")
        
        if current_size != expected_size:
            return False, (
                f"Size mismatch for {model_name}:{version}\n"
                f"Expected: {expected_size} bytes\n"
                f"Got:      {current_size} bytes"
            )
        
        # Mark as verified
        manifest_data["verified"] = True
        manifest_data["verified_by"] = verifier_id or "system"
        manifest_data["verified_at"] = datetime.utcnow().isoformat()
        
        with open(manifest_file, 'w') as f:
            json.dump(manifest_data, f, indent=2)
        
        return True, f"{model_name}:{version} verified successfully"
    
    def verify_all_models(self, models: Dict[str, str]) -> Dict[str, Tuple[bool, str]]:
        """
        Verify multiple models.
        
        Args:
            models: Dict of {model_name: file_path}
            
        Returns:
            Dict of {model_name: (is_valid, message)}
        """
        results = {}
        for model_name, file_path in models.items():
            # Try to extract version from file path or use "latest"
            version = "latest"
            is_valid, message = self.verify_model(
                model_name, version, file_path, verifier_id="batch"
            )
            results[model_name] = (is_valid, message)
        
        return results


def verify_models_at_boot(
    models: Dict[str, str],
    manifest_dir: Path = Path("/var/lib/model-manifests"),
) -> Dict[str, Tuple[bool, str]]:
    """
    Boot-time model verification.
    
    Call this on startup to ensure models haven't been tampered with.
    """
    verifier = ModelVerifier(manifest_dir)
    return verifier.verify_all_models(models)
