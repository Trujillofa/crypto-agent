# Config Doctor

`config_doctor` is a static validator for `config/settings*.yaml` files.

It catches high-risk or unreachable configuration states before they hit runtime, including:

- Impossible aggregator thresholds (for example, a buy threshold that no strategy set can ever reach)
- Futures routing mismatches (`default_trading_mode=futures` while `futures.enabled=false`)
- Unused per-symbol overrides
- Risky execution settings (paper mode with live execution flags, invalid ATR sizing values)
- Missing optional service secrets when the service is enabled

## Usage

Validate all settings files:

```bash
python scripts/config_doctor.py --all
```

Validate one file:

```bash
python scripts/config_doctor.py --config config/settings.yaml
```

Emit JSON for CI tooling:

```bash
python scripts/config_doctor.py --all --json
```

Fail behavior:

- `--fail-on error` (default): non-zero exit only on errors
- `--fail-on warning`: non-zero exit on warnings or errors
- `--fail-on none`: always exit zero

## CI Example

```bash
python scripts/config_doctor.py --all --fail-on warning
```

This makes misconfigurations block deployment before they can cause signal droughts or unsafe trading modes.
