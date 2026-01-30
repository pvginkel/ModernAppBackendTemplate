# Change Brief: Container Refactor

## Summary

Move all service definitions from `CommonContainer` to `AppContainer` to fix inheritance issues when apps override `config` with a more specific Settings type.

## Problem

The current container inheritance model causes `RecursionError` or `NameError` when:
1. `CommonContainer` defines services that reference `config`
2. `AppContainer` overrides `config` with an app-specific `Settings` type
3. dependency-injector's resolution gets confused by cross-class references

## Solution

Keep `CommonContainer` as a minimal base with only `Dependency` declarations (placeholders). Move all service definitions to `AppContainer` where they can safely reference `config`.

## Scope

- `template/common/core/container.py.jinja` - Remove service definitions, keep only Dependency declarations
- `template/app/container.py.jinja` - Add all service definitions with proper Jinja conditionals

## Requirements

- [ ] CommonContainer only has Dependency declarations (config, session_maker) and derived db_session
- [ ] AppContainer defines all services: shutdown_coordinator, metrics_service, metrics_coordinator, task_service
- [ ] AppContainer conditionally defines: connection_manager (use_sse), s3_service (use_s3), oidc_authenticator/oidc_client (use_oidc)
- [ ] All existing tests pass after regenerating test-app
- [ ] Changelog updated with migration instructions
