from __future__ import annotations

from services.sol_usd_basis import refresh_sol_usd_basis


def main() -> int:
    result = refresh_sol_usd_basis(timeout=2.5)
    if result.get("value") is None:
        print(f"[FAIL] SOL/USD basis refresh: {result.get('error')}")
        return 1
    print(
        "[PASS] SOL/USD basis refreshed "
        f"value=${float(result['value']):.4f} source={result.get('source')} age=0s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
