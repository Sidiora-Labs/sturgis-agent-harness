"""Tenant settings, atomic request admission and credential-free configuration transfer."""
from __future__ import annotations

import secrets
import sqlite3
import time

from model_gateway.catalog import PROTOCOLS, normalize_endpoint
from model_gateway.errors import GatewayError
from model_gateway.store import text
from model_gateway.wire import json_dumps, json_loads

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
SETTINGS_FIELDS = frozenset({'request_timeout', 'max_concurrency', 'requests_per_minute'})
MODEL_FIELDS = frozenset({'id', 'name', 'context_limit', 'output_limit', 'tools', 'protocol', 'limits_source', 'tools_source'})


def _settings_value(value, *, timeout=300, concurrency=128):
    """Validate portable limits independently from the destination operator ceiling."""
    if (not isinstance(value, dict) or set(value) != SETTINGS_FIELDS
            or type(value['request_timeout']) not in (int, float) or not 0 < value['request_timeout'] <= timeout
            or type(value['max_concurrency']) is not int or not 1 <= value['max_concurrency'] <= concurrency
            or type(value['requests_per_minute']) is not int or not 1 <= value['requests_per_minute'] <= 10000):
        raise GatewayError('invalid_settings', 'Use a positive timeout and concurrency within operator limits, and 1 to 10,000 requests per minute.')
    return {key: value[key] for key in SETTINGS_FIELDS}


def _identifier(value, label, maximum=256):
    """Keep model namespace separators while rejecting ambiguous or control-bearing IDs."""
    clean = text(value, label, maximum)
    if clean != value or any(c.isspace() or c == '\\' or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in clean):
        raise GatewayError('invalid_import', f'Use an unambiguous {label} without whitespace or control characters.')
    return clean


def _bounded_archive(archive):
    """Snapshot finite JSON before validation; the caller cannot mutate the admitted body."""
    try:
        encoded = json_dumps(archive).encode()
    except (GatewayError, UnicodeError):
        raise GatewayError('invalid_import', 'The configuration must contain finite, valid UTF-8 JSON.') from None
    if len(encoded) > MAX_ARCHIVE_BYTES:
        raise GatewayError('invalid_import', 'Choose a gateway configuration export up to 2 MiB.')
    return json_loads(encoded)


class GatewayControls:
    """Operational state belongs to the gateway, independently of every client."""

    def __init__(self, gateway, max_concurrency):
        self.gateway, self.store = gateway, gateway.store
        self.defaults = {'request_timeout': self.gateway.network.timeout, 'max_concurrency': max_concurrency, 'requests_per_minute': 120}
        self.clock = time.time

    def _project_settings(self, row):
        """Apply reduced operator ceilings immediately without rewriting saved tenant intent."""
        stored = _settings_value({key: row.get(key, self.defaults[key]) for key in SETTINGS_FIELDS})
        stored['request_timeout'] = min(stored['request_timeout'], self.defaults['request_timeout'])
        stored['max_concurrency'] = min(stored['max_concurrency'], self.defaults['max_concurrency'])
        return {**stored, 'revision': row.get('revision', 0), 'operator_limits': {
            'request_timeout': self.defaults['request_timeout'], 'max_concurrency': self.defaults['max_concurrency']}}

    def settings(self, tenant):
        """Return effective limits and the exact saved revision for compare-and-swap edits."""
        try:
            row = self.store.get(tenant, 'settings', 'active')
        except GatewayError as error:
            if error.code != 'not_found':
                raise
            row = {}
        return self._project_settings(row)

    def save_settings(self, tenant, *, expected_revision, request_timeout, max_concurrency, requests_per_minute):
        """Save only the reviewed revision; edits never reset the current request count."""
        value = _settings_value({'request_timeout': request_timeout, 'max_concurrency': max_concurrency,
                                'requests_per_minute': requests_per_minute},
                               timeout=self.defaults['request_timeout'], concurrency=self.defaults['max_concurrency'])
        row = self.store.put(tenant, 'settings', 'active', value, expected_revision)
        return self._project_settings(row)

    def admit(self, tenant):
        """Read settings and count an authenticated request in one cross-process transaction."""
        tenant = self.store._tenant(tenant)
        now = int(self.clock() // 60)
        with self.store.transaction() as db:
            settings = db.execute("SELECT * FROM objects WHERE tenant=? AND kind='settings' AND id='active'", (tenant,)).fetchone()
            config = self._project_settings(self.store._row(settings) if settings else {})
            row = db.execute("SELECT * FROM objects WHERE tenant=? AND kind='rate_window' AND id='active'", (tenant,)).fetchone()
            value = json_loads(row['body']) if row else {}
            if row and (not isinstance(value, dict) or set(value) != {'window', 'count'}
                        or type(value['window']) is not int or value['window'] < 0
                        or type(value['count']) is not int or not 0 <= value['count'] <= 10000):
                raise GatewayError('invalid_rate_state', 'The gateway request counter needs operator recovery.', 503)
            # A backward wall-clock adjustment cannot grant another fresh window.
            window = max(now, value.get('window', now))
            count = value.get('count', 0) if value.get('window') == window else 0
            if count >= config['requests_per_minute']:
                raise GatewayError('rate_limited', 'Your gateway request limit has been reached. Wait for the next minute or adjust gateway settings.', 429, retryable=True)
            db.execute("INSERT INTO objects VALUES (?,'rate_window','active',?,?) ON CONFLICT(tenant,kind,id) DO UPDATE SET revision=excluded.revision,body=excluded.body",
                (tenant, row['revision'] + 1 if row else 1, json_dumps({'window': window, 'count': count + 1})))
        return config

    def export(self, tenant):
        """Take one consistent metadata snapshot, excluding tokens, app keys and runtime authority."""
        tenant = self.store._tenant(tenant)
        with self.store.transaction() as db:
            rows = [(row['kind'], self.store._row(row)) for row in db.execute("SELECT * FROM objects WHERE tenant=? AND kind IN ('connection','route','settings') ORDER BY kind,id", (tenant,))]
        connections, routes, settings = [], [], dict(self.defaults)
        for kind, row in rows:
            if kind == 'connection':
                connection = {key: row[key] for key in ('id', 'name', 'preset_id', 'endpoint', 'protocol', 'selected_model', 'catalog_mode') if key in row}
                connection['models'] = [{key: model[key] for key in MODEL_FIELDS if key in model} for model in row['models']]
                connections.append(connection)
            elif kind == 'route':
                routes.append({key: row[key] for key in ('id', 'name', 'models', 'strategy')})
            else:
                effective = self._project_settings(row)
                settings = {key: effective[key] for key in SETTINGS_FIELDS}
        if len(connections) > 256 or len(routes) > 256:
            raise GatewayError('export_bounds', 'Configuration transfer supports up to 256 connections and 256 routes per file.')
        return _bounded_archive({'format': 'sturgis-gateway-config/1', 'created_at': self.clock(), 'credentials_included': False,
                                 'connections': connections, 'routes': routes, 'settings': settings})

    def _import_models(self, raw, preset):
        """Reject ambiguous model metadata before creating aliases or retaining a selection."""
        if not isinstance(raw, list) or not 1 <= len(raw) <= 256:
            raise GatewayError('invalid_import', 'Each imported connection needs between 1 and 256 models.')
        protocols = set(PROTOCOLS) if preset['protocol'] in PROTOCOLS else {preset['protocol']}
        if preset['id'] == 'codex':
            protocols = {'openai-responses'}
        elif preset['id'] == 'github-copilot':
            protocols = {'openai', 'anthropic', 'openai-responses'}
        seen, result = set(), []
        for value in raw:
            if not isinstance(value, dict) or 'id' not in value or set(value) - MODEL_FIELDS:
                raise GatewayError('invalid_import', 'The export contains an invalid model record.')
            id = _identifier(value['id'], 'model identifier')
            context, output = value.get('context_limit', preset['defaults']['context_limit']), value.get('output_limit', preset['defaults']['output_limit'])
            protocol = value.get('protocol', preset['protocol'])
            if (id in seen or type(context) is not int or not 128 <= context <= 2000000
                    or type(output) is not int or not 1 <= output < context
                    or type(value.get('tools', preset['defaults']['tools'])) is not bool
                    or not isinstance(protocol, str) or protocol not in protocols
                    or value.get('limits_source', 'preset_default') not in ('provider_reported', 'preset_default', 'preset_default_manual_import')
                    or value.get('tools_source', 'preset_default') not in ('provider_reported', 'preset_default')):
                raise GatewayError('invalid_import', 'Model identifiers, protocols, capabilities and token limits must be valid and distinct.')
            seen.add(id)
            model = self.gateway._models([value], preset)[0]
            for field in ('limits_source', 'tools_source'):
                if field in value:
                    model[field] = value[field]
            result.append(model)
        return result

    def import_configuration(self, tenant, archive):
        """Restore new disabled connections atomically; credentials and saved limits are never applied."""
        tenant = self.store._tenant(tenant)
        archive = _bounded_archive(archive)
        if (not isinstance(archive, dict) or set(archive) != {'format', 'created_at', 'credentials_included', 'connections', 'routes', 'settings'}
                or archive['format'] != 'sturgis-gateway-config/1' or archive['credentials_included'] is not False
                or type(archive['created_at']) not in (int, float) or not 0 < archive['created_at'] <= 10000000000
                or not isinstance(archive['connections'], list) or len(archive['connections']) > 256
                or not isinstance(archive['routes'], list) or len(archive['routes']) > 256):
            raise GatewayError('invalid_import', 'Choose a valid credential-free gateway configuration export up to 2 MiB.')
        try:
            _settings_value(archive['settings'])
        except GatewayError:
            raise GatewayError('invalid_import', 'The export contains invalid gateway settings metadata.') from None
        connections, routes, mapping, aliases, route_ids = [], [], {}, {}, set()
        for row in archive['connections']:
            if (not isinstance(row, dict) or set(row) - {'id', 'name', 'preset_id', 'endpoint', 'protocol', 'models', 'selected_model', 'catalog_mode'}
                    or not {'id', 'name', 'preset_id', 'endpoint', 'protocol', 'models'} <= set(row)):
                raise GatewayError('invalid_import', 'The export contains an invalid provider connection.')
            old_id = _identifier(row['id'], 'connection identifier', 128)
            if old_id in mapping or '/' in old_id:
                raise GatewayError('invalid_import', 'Connection identifiers in the export must be distinct and contain no slash.')
            preset = self.gateway._preset(row['preset_id'])
            endpoint = normalize_endpoint(row['endpoint'], preset['protocol'] if preset['protocol'] in PROTOCOLS else 'openai')
            if row['protocol'] != preset['protocol'] or row.get('catalog_mode', 'discover') not in ('discover', 'manual'):
                raise GatewayError('invalid_import', 'A provider protocol or catalog mode does not match its preset.')
            models = self._import_models(row['models'], preset)
            selected = row.get('selected_model', models[0]['id'])
            if not isinstance(selected, str) or selected not in {model['id'] for model in models}:
                raise GatewayError('invalid_import', 'The selected model must occur in the imported model list.')
            id = 'conn_' + secrets.token_hex(8)
            mapping[old_id] = id
            for model in models:
                aliases[old_id + '/' + model['id']] = id + '/' + model['id']
            connections.append((id, {'name': text(row['name'], 'connection name', 120), 'preset_id': preset['id'],
                'endpoint': endpoint, 'protocol': preset['protocol'], 'models': models, 'selected_model': selected,
                'catalog_mode': row.get('catalog_mode', 'discover'), 'auth_account_id': None, 'secret_ref': None,
                'status': 'error', 'discovered_at': None, 'error': {'code': 'reconnect_required',
                'message': 'Imported configuration. Reconnect this provider before using its models.'}}))
        for row in archive['routes']:
            if (not isinstance(row, dict) or set(row) != {'id', 'name', 'models', 'strategy'}
                    or row['strategy'] not in ('priority', 'round_robin') or not isinstance(row['models'], list)
                    or not 1 <= len(row['models']) <= 16 or any(not isinstance(m, str) or m not in aliases for m in row['models'])
                    or len(set(row['models'])) != len(row['models'])):
                raise GatewayError('invalid_import', 'Routes must point only to distinct models in this export.')
            route_id = _identifier(row['id'], 'route identifier', 64)
            if route_id in route_ids or '/' in route_id or route_id.startswith('conn_'):
                raise GatewayError('invalid_import', 'The export contains an invalid or duplicate route identifier.')
            route_ids.add(route_id)
            routes.append(('imported-' + secrets.token_hex(6), {'name': text(row['name'], 'route name', 120),
                'models': [aliases[m] for m in row['models']], 'strategy': row['strategy']}))
        try:
            with self.store.transaction() as db:
                for kind, items in (('connection', connections), ('route', routes)):
                    for id, value in items:
                        db.execute('INSERT INTO objects VALUES (?,?,?,1,?)', (tenant, kind, id, json_dumps(value)))
        except sqlite3.IntegrityError:
            raise GatewayError('import_conflict', 'The configuration changed during import. Retry the import.', 409) from None
        return {'imported_connections': len(connections), 'imported_routes': len(routes), 'reconnection_required': True,
                'settings_applied': False, 'connection_ids': mapping}
