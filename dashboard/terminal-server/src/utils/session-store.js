const fs = require('fs').promises;
const fsSync = require('fs');
const path = require('path');
const os = require('os');

class SessionStore {
    constructor(options = {}) {
        // Store sessions in user's home directory
        this.storageDir = options.storageDir || path.join(os.homedir(), '.claude-code-web');
        this.sessionsFile = path.join(this.storageDir, 'sessions.json');
        this.sessionTtlMs = options.sessionTtlMs ?? (24 * 60 * 60 * 1000);
        this.maxFileAgeDays = options.maxFileAgeDays ?? 7;
        fsSync.mkdirSync(this.storageDir, { recursive: true });
        this.initializeStorage();
    }

    async initializeStorage() {
        try {
            // Create storage directory if it doesn't exist
            await fs.mkdir(this.storageDir, { recursive: true });
        } catch (error) {
            console.error('Failed to create storage directory:', error);
        }
    }

    async saveSessions(sessions) {
        try {
            // Ensure storage directory exists
            await fs.mkdir(this.storageDir, { recursive: true });

            const now = Date.now();
            
            // Convert Map to array for JSON serialization
            const sessionsArray = Array.from(sessions.entries())
                .filter(([, session]) => !this.isSessionStale(session, now))
                .map(([id, session]) => ({
                    id,
                    name: session.name || 'Unnamed Session',
                    created: session.created || new Date(),
                    lastActivity: session.lastActivity || new Date(),
                    workingDir: session.workingDir || process.cwd(),
                    agentName: session.agentName || null,
                    active: false, // Always set to false when saving (processes won't persist)
                    outputBuffer: Array.isArray(session.outputBuffer) ? session.outputBuffer.slice(-100) : [], // Keep last 100 lines
                    connections: [], // Clear connections (they won't persist)
                    lastAccessed: session.lastAccessed || Date.now(),
                    // Chat mode data
                    mode: session.mode || null,
                    chatHistory: Array.isArray(session.chatHistory) ? session.chatHistory.slice(-50) : [], // Keep last 50 messages
                    sdkSessionId: session.sdkSessionId || null,
                    // Ticket binding (Feature 1.3)
                    ticketId: session.ticketId || null,
                    // Archive flag
                    archived: session.archived || false,
                    // Session-specific usage tracking
                    sessionStartTime: session.sessionStartTime || null,
                    sessionUsage: session.sessionUsage || {
                        requests: 0,
                        inputTokens: 0,
                        outputTokens: 0,
                        cacheTokens: 0,
                        totalCost: 0,
                        models: {}
                    }
                }));

            const data = {
                version: '1.0',
                savedAt: new Date().toISOString(),
                sessions: sessionsArray
            };

            // Write to a temporary file first, then rename (atomic operation)
            const tempFile = `${this.sessionsFile}.tmp`;
            await fs.writeFile(tempFile, JSON.stringify(data, null, 2));
            // Ensure directory still exists before rename (handles race conditions)
            await fs.mkdir(this.storageDir, { recursive: true });
            await fs.rename(tempFile, this.sessionsFile);
            
            return true;
        } catch (error) {
            console.error('Failed to save sessions:', error.message);
            return false;
        }
    }

    async loadSessions() {
        try {
            // Check if sessions file exists
            await fs.access(this.sessionsFile);
            
            const data = await fs.readFile(this.sessionsFile, 'utf8');
            
            // Check if file is empty or just whitespace
            if (!data || !data.trim()) {
                console.log('Sessions file is empty, starting fresh');
                return new Map();
            }
            
            let parsed;
            try {
                parsed = JSON.parse(data);
            } catch (parseError) {
                console.error('Sessions file is corrupted, starting fresh:', parseError.message);
                // Try to backup the corrupted file
                try {
                    await fs.rename(this.sessionsFile, `${this.sessionsFile}.corrupted.${Date.now()}`);
                } catch (renameError) {
                    // Ignore rename errors
                }
                return new Map();
            }
            
            // Validate parsed data structure
            if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.sessions)) {
                console.log('Invalid sessions file format, starting fresh');
                return new Map();
            }
            
            // Check if data is recent (within last 7 days)
            if (parsed.savedAt) {
                const savedAt = new Date(parsed.savedAt);
                const now = new Date();
                const daysSinceSave = (now - savedAt) / (1000 * 60 * 60 * 24);
                
                if (daysSinceSave > this.maxFileAgeDays) {
                    console.log('Sessions are too old, starting fresh');
                    return new Map();
                }
            }

            // Convert array back to Map
            const sessions = new Map();
            let droppedStale = 0;
            for (const session of parsed.sessions) {
                if (!session || !session.id) continue; // Skip invalid sessions

                if (this.isSessionStale(session, Date.now(), parsed.savedAt)) {
                    droppedStale += 1;
                    continue;
                }
                
                // Synthesize uuids for legacy chatHistory entries that lack them
                const chatHistory = (session.chatHistory || []).map((msg, i) => {
                    if (!msg.uuid) {
                        return { ...msg, uuid: `legacy-${session.id}-${i}` };
                    }
                    return msg;
                });

                // Restore session with default values for runtime properties
                sessions.set(session.id, {
                    ...session,
                    created: session.created ? new Date(session.created) : new Date(),
                    lastActivity: session.lastActivity ? new Date(session.lastActivity) : new Date(),
                    active: false,
                    connections: new Set(),
                    outputBuffer: session.outputBuffer || [],
                    maxBufferSize: 1000,
                    // Restore chat data
                    chatHistory,
                    sdkSessionId: session.sdkSessionId || null,
                    mode: session.mode || null,
                    // Restore usage data if available
                    usageData: session.usageData || null
                });
            }

            if (droppedStale > 0) {
                console.log(`Dropped ${droppedStale} stale sessions from disk`);
            }
            console.log(`Restored ${sessions.size} sessions from disk`);
            return sessions;
        } catch (error) {
            // File doesn't exist or other errors, return empty Map
            if (error.code !== 'ENOENT') {
                console.error('Failed to load sessions:', error.message);
            }
            return new Map();
        }
    }

    async clearOldSessions() {
        try {
            await fs.unlink(this.sessionsFile);
            console.log('Cleared old sessions');
            return true;
        } catch (error) {
            if (error.code !== 'ENOENT') {
                console.error('Failed to clear sessions:', error);
            }
            return false;
        }
    }

    async getSessionMetadata() {
        try {
            await fs.access(this.sessionsFile);
            const stats = await fs.stat(this.sessionsFile);
            const data = await fs.readFile(this.sessionsFile, 'utf8');
            const parsed = JSON.parse(data);
            
            return {
                exists: true,
                savedAt: parsed.savedAt,
                sessionCount: parsed.sessions ? parsed.sessions.length : 0,
                fileSize: stats.size,
                version: parsed.version
            };
        } catch (error) {
            return {
                exists: false,
                error: error.message
            };
        }
    }

    _sessionTouchTimestamp(session, fallbackSavedAt = null) {
        const candidates = [
            session?.lastActivity,
            session?.lastAccessed,
            session?.created,
            fallbackSavedAt,
        ];
        for (const candidate of candidates) {
            if (candidate === null || candidate === undefined || candidate === '') continue;
            const value = candidate instanceof Date ? candidate.getTime() : new Date(candidate).getTime();
            if (Number.isFinite(value)) return value;
        }
        return null;
    }

    isSessionStale(session, now = Date.now(), fallbackSavedAt = null) {
        if (!session || session.archived || session.active) return false;
        if (session.connections instanceof Set ? session.connections.size > 0 : Array.isArray(session.connections) && session.connections.length > 0) {
            return false;
        }
        const lastTouch = this._sessionTouchTimestamp(session, fallbackSavedAt);
        if (lastTouch === null) return false;
        return (now - lastTouch) > this.sessionTtlMs;
    }
}

module.exports = SessionStore;
