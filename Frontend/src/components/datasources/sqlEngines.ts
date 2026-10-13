/**
 * What each database engine supports, mirrored from
 * `backend/datasources/connection.py`. The backend is the authority (it refuses
 * an unsupported combination with a coded error); this copy only keeps the form
 * from offering a choice that would be refused.
 */
import type { SqlEngine, SqlSslMode } from '@/lib/types'

export const ENGINES: SqlEngine[] = ['postgresql', 'mysql', 'mssql', 'oracle', 'mongodb']

/** Engines read through their own client, never the SQL editor. */
export const DOCUMENT_ENGINES: SqlEngine[] = ['mongodb']

export const DEFAULT_PORT: Record<SqlEngine, number> = {
  postgresql: 5432, mysql: 3306, mssql: 1433, oracle: 1521, mongodb: 27017,
}

/** What a connection saved before TLS modes existed did — and what a new form proposes. */
export const DEFAULT_SSL_MODE: Record<SqlEngine, SqlSslMode> = {
  postgresql: 'prefer', mysql: 'prefer', mssql: 'require', oracle: 'disable', mongodb: 'disable',
}

export const SSL_MODES: Record<SqlEngine, SqlSslMode[]> = {
  postgresql: ['disable', 'prefer', 'require', 'verify-ca', 'verify-full'],
  mysql: ['disable', 'prefer', 'require', 'verify-ca', 'verify-full'],
  mssql: ['disable', 'prefer', 'require', 'verify-full'],
  oracle: ['disable', 'require', 'verify-ca', 'verify-full'],
  mongodb: ['disable', 'require', 'verify-ca', 'verify-full'],
}

/** Engines whose driver can load an uploaded CA certificate. */
export const CA_ENGINES: SqlEngine[] = ['postgresql', 'mysql', 'oracle', 'mongodb']

export const MAX_CA_BYTES = 64 * 1024

export const TIMEOUTS = {
  connect: { min: 1, max: 60, def: 10 },
  statement: { min: 1, max: 600, def: 30 },
}

export function engineLabelKey(engine: SqlEngine): string {
  return `data.conn.engine_${engine}`
}
