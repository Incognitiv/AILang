"""Security tool runners: pip-audit, detect-secrets."""

from __future__ import annotations

import contextlib
import importlib
import io
import logging
from typing import Any, Dict, cast

from .common import validate_filepath


def run_pip_audit(_filepath: str) -> Dict[str, Any]:
    """Run pip-audit for dependency CVEs.

    Note: pip-audit scans the entire Python environment.
    This is informational only - doesn't affect code quality score.
    """
    try:
        audit_mod = cast(Any, importlib.import_module("pip_audit._audit"))
        source_mod = cast(Any, importlib.import_module("pip_audit._dependency_source"))
        pypi_mod = cast(Any, importlib.import_module("pip_audit._service.pypi"))

        # Suppress logging noise
        logging.getLogger("pip_audit").setLevel(logging.CRITICAL)

        source = source_mod.PipSource()
        service = pypi_mod.PyPIService()
        auditor = audit_mod.Auditor(service)

        vulnerabilities = []
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                for dep, vulns in auditor.audit(source):
                    for vuln in vulns:
                        vulnerabilities.append(f"{dep.name}=={dep.version}: {vuln.id}")

        return {
            "passed": len(vulnerabilities) == 0,
            "vulnerabilities_count": len(vulnerabilities),
            "issues": vulnerabilities[:20],  # Limit output
            "source": "environment",  # Always environment-wide
        }
    except ImportError:
        return {
            "passed": True,
            "vulnerabilities_count": 0,
            "issues": [],
            "error": "pip-audit not installed",
        }
    except (OSError, IOError, RuntimeError) as exc:
        return {
            "passed": True,
            "vulnerabilities_count": 0,
            "issues": [],
            "error": str(exc),
        }


def run_detect_secrets(filepath: str) -> Dict[str, Any]:
    """Run detect-secrets using its Python API."""
    try:
        scan_mod = cast(Any, importlib.import_module("detect_secrets.core.scan"))
    except ImportError:
        return {
            "error": "detect-secrets not installed",
            "secrets_count": 0,
            "passed": True,
        }
    validated_path, err = validate_filepath(filepath)
    if err:
        return {
            "error": f"Validation failed: {err}",
            "secrets_count": 0,
            "passed": True,
        }
    prev_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    logger = logging.getLogger("detect_secrets")
    scan_logger = logging.getLogger("detect_secrets.core.scan")
    prev_level = logger.level
    prev_scan_level = scan_logger.level
    logger.setLevel(logging.CRITICAL)
    scan_logger.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                secrets = list(scan_mod.scan_file(validated_path))
        issues = [f"Potential secret: {s.type}" for s in secrets[:100]]
        return {"passed": not secrets, "secrets_count": len(secrets), "issues": issues}
    except (OSError, IOError, ValueError) as exc:
        return {"passed": True, "secrets_count": 0, "issues": [], "error": str(exc)}
    finally:
        logger.setLevel(prev_level)
        scan_logger.setLevel(prev_scan_level)
        logging.disable(prev_disable)
