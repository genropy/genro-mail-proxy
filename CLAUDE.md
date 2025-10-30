# Claude Code Instructions - Genro Mail Proxy

**Parent Document**: This project follows all policies from the central [genro-next-generation CLAUDE.md](https://github.com/genropy/genro-next-generation/blob/main/CLAUDE.md)

Read the parent document first for:
- Language policy (English only)
- Git commit authorship rules (no Claude co-author)
- Development status lifecycle (Pre-Alpha → Alpha → Beta)
- Temporary files policy (use temp/ directories)
- Standardization requirements
- All general project policies

## Project-Specific Context

### Current Status
- **Development Status**: Beta
- **Has Implementation Code**: Yes (4500+ lines)
- **GitHub**: https://github.com/genropy/genro-mail-proxy

### Project Purpose
Asynchronous email dispatcher microservice with scheduling, rate limiting, attachments, and FastAPI REST API. A production-ready service for email handling in the Genropy ecosystem.

### Architecture

- **REST API** (FastAPI) for control and message submission
- **SQLite persistence** with automatic retry and reporting
- **Background loops** for dispatch and client reporting
- **Pluggable attachments** via genro-storage integration
- **Prometheus metrics** for monitoring

### Key Features

- Multi-SMTP provider support
- Priority-based queuing
- Rate limiting (global and per-account)
- Attachment handling (local, S3, HTTP)
- Automatic retry on failure
- Client notification callbacks

### Development Guidelines

- Follow async/await patterns consistently
- Maintain test coverage for new features
- Update Prometheus metrics for new operations
- Document API changes in docstrings

### Dependencies

- Requires `genro-storage` for attachment handling
- Uses SQLite for persistence (async with aiosqlite)
- FastAPI for REST API
- aiosmtplib for SMTP operations

### Project-Specific Guidelines

**Beta Status Notes:**
- Core features are implemented and functional
- API is stabilizing but breaking changes may still occur
- Focus on testing, refinement, and stability
- Documentation should reflect current beta state
- Backward compatibility not yet required

---

**All general policies are inherited from the parent document: [genro-next-generation CLAUDE.md](https://github.com/genropy/genro-next-generation/blob/main/CLAUDE.md)**

**Last Updated**: 2025-10-30
