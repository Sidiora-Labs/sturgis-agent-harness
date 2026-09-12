#include "engine.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <limits>
#include <set>
#include <sstream>
#include <thread>
#include <vector>

namespace sturgis {
namespace {
[[noreturn]] void invalid(const std::string& message) {
    throw Failure("invalid_request", "validation", message);
}
void require(bool ok, const std::string& message) { if (!ok) invalid(message); }
void fields(const Json& value, std::initializer_list<const char*> required,
            std::initializer_list<const char*> optional = {}) {
    require(value.is_object(), "expected object");
    std::set<std::string> allowed;
    for (const auto* name : required) {
        require(value.contains(name), std::string("missing field: ") + name);
        allowed.insert(name);
    }
    for (const auto* name : optional) allowed.insert(name);
    for (auto item = value.begin(); item != value.end(); ++item)
        require(allowed.contains(item.key()), "unknown field: " + item.key());
}
std::string string_value(const Json& value, std::size_t maximum = 256, bool empty = false) {
    require(value.is_string(), "expected string");
    auto text = value.get<std::string>();
    require((empty || !text.empty()) && text.size() <= maximum && text.find('\0') == text.npos,
            "invalid string length or embedded NUL");
    return text;
}
long long integer(const Json& value, long long minimum = 0, long long maximum = 9007199254740991LL) {
    require(value.is_number_integer(), "expected integer");
    if (value.is_number_unsigned())
        require(value.get<unsigned long long>() <= static_cast<unsigned long long>(maximum), "integer exceeds bound");
    auto number = value.get<long long>();
    require(number >= minimum && number <= maximum, "integer outside bounds");
    return number;
}
double number(const Json& value) {
    require(value.is_number(), "expected finite number");
    auto result = value.get<double>();
    require(std::isfinite(result), "expected finite number");
    return result;
}
double now() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}
void concepts_valid(const Json& values) {
    require(values.is_array() && values.size() <= 64, "invalid concepts");
    std::set<std::string> seen;
    for (const auto& value : values)
        require(seen.insert(string_value(value)).second, "duplicate concept");
}
void embedding_valid(const Json& embedding) {
    fields(embedding, {"model", "vector"});
    string_value(embedding["model"]);
    const auto& values = embedding["vector"];
    require(values.is_array() && !values.empty() && values.size() <= 2048, "invalid vector dimension");
    long double norm = 0;
    for (const auto& value : values) {
        auto coordinate = number(value);
        norm += static_cast<long double>(coordinate) * coordinate;
    }
    require(norm > 0 && std::isfinite(norm), "invalid vector norm");
}
void record_valid(const Json& record) {
    fields(record, {"id", "kind", "content", "sources", "source_time", "observed_at", "valid_from", "concepts"},
           {"valid_to", "embedding"});
    string_value(record["id"]);
    static const std::set<std::string> kinds{"fact", "experience", "observation", "belief", "procedure"};
    require(kinds.contains(string_value(record["kind"])), "invalid memory kind");
    string_value(record["content"], 32768);
    const auto& sources = record["sources"];
    require(sources.is_array() && !sources.empty() && sources.size() <= 64, "invalid source evidence");
    for (const auto& source : sources) {
        fields(source, {"id", "revision", "start", "end"});
        string_value(source["id"]);
        integer(source["revision"], 1);
        require(integer(source["end"], 1) > integer(source["start"]), "empty source range");
    }
    require(number(record["source_time"]) >= 0 && number(record["observed_at"]) >= 0
            && number(record["valid_from"]) >= 0, "negative timestamp");
    if (record.contains("valid_to") && !record["valid_to"].is_null())
        require(number(record["valid_to"]) > number(record["valid_from"]), "invalid temporal interval");
    concepts_valid(record["concepts"]);
    if (record.contains("embedding")) embedding_valid(record["embedding"]);
}
class Statement {
    sqlite3* db_;
    sqlite3_stmt* stmt_ = nullptr;
public:
    Statement(sqlite3* db, const std::string& sql) : db_(db) {
        if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt_, nullptr) != SQLITE_OK)
            throw Failure("unavailable", "storage", sqlite3_errmsg(db));
    }
    ~Statement() { sqlite3_finalize(stmt_); }
    Statement(const Statement&) = delete;
    void bind(int i, const std::string& value) { sqlite3_bind_text(stmt_, i, value.data(), value.size(), SQLITE_TRANSIENT); }
    void bind(int i, long long value) { sqlite3_bind_int64(stmt_, i, value); }
    void bind(int i, double value) { sqlite3_bind_double(stmt_, i, value); }
    bool step() {
        auto result = sqlite3_step(stmt_);
        if (result != SQLITE_ROW && result != SQLITE_DONE)
            throw Failure("unavailable", "storage", sqlite3_errmsg(db_));
        return result == SQLITE_ROW;
    }
    long long integer(int i = 0) const { return sqlite3_column_int64(stmt_, i); }
    double real(int i = 0) const { return sqlite3_column_double(stmt_, i); }
    std::string text(int i = 0) const {
        auto* value = sqlite3_column_text(stmt_, i);
        return value ? std::string(reinterpret_cast<const char*>(value), sqlite3_column_bytes(stmt_, i)) : "";
    }
};
void sql(sqlite3* db, const std::string& text) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    for (;;) {
        char* error = nullptr;
        auto code = sqlite3_exec(db, text.c_str(), nullptr, nullptr, &error);
        if (code == SQLITE_OK) return;
        std::string message = error ? error : "SQLite operation failed";
        sqlite3_free(error);
        if ((code == SQLITE_BUSY || code == SQLITE_LOCKED) && std::chrono::steady_clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            continue;
        }
        throw Failure("unavailable", "storage", message);
    }
}
class Transaction {
    sqlite3* db_;
    bool committed_ = false;
public:
    explicit Transaction(sqlite3* db) : db_(db) { sql(db_, "BEGIN IMMEDIATE"); }
    ~Transaction() { if (!committed_) sqlite3_exec(db_, "ROLLBACK", nullptr, nullptr, nullptr); }
    void commit() { sql(db_, "COMMIT"); committed_ = true; }
};
std::set<std::string> tokens(const std::string& text) {
    std::set<std::string> result;
    std::string word;
    for (unsigned char character : text) {
        if (std::isalnum(character) || character >= 128) word += static_cast<char>(std::tolower(character));
        else if (!word.empty()) { result.insert(word); word.clear(); }
    }
    if (!word.empty()) result.insert(word);
    return result;
}
double overlap(const std::set<std::string>& wanted, const std::set<std::string>& present) {
    if (wanted.empty()) return 0;
    unsigned hits = 0;
    for (const auto& item : wanted) hits += present.contains(item);
    return static_cast<double>(hits) / wanted.size();
}
double cosine(const Json& left, const Json& right) {
    long double dot = 0, a = 0, b = 0;
    for (std::size_t i = 0; i < left.size(); ++i) {
        long double x = left[i].get<double>(), y = right[i].get<double>();
        dot += x*y; a += x*x; b += y*y;
    }
    return static_cast<double>(dot / (std::sqrt(a) * std::sqrt(b)));
}
}

Json strict_json(const std::string& text) {
    std::vector<std::set<std::string>> keys;
    return Json::parse(text, [&keys](int depth, Json::parse_event_t event, Json& value) {
        require(depth <= 32, "JSON nesting exceeds bound");
        if (event == Json::parse_event_t::object_start) keys.emplace_back();
        if (event == Json::parse_event_t::key)
            require(keys.back().insert(value.get<std::string>()).second, "duplicate JSON key");
        if (event == Json::parse_event_t::object_end) keys.pop_back();
        return true;
    });
}
Json failure_response(const Json& request_id, const std::string& status,
                      const std::string& code, const std::string& message) {
    return {{"protocol_version", "memory/1"}, {"request_id", request_id}, {"status", status},
            {"data", nullptr}, {"error", {{"code", code}, {"message", message}}},
            {"generation", nullptr}, {"watermark", nullptr}};
}

Engine::Engine(const std::string& path) {
    sqlite3* opened = nullptr;
    auto result = sqlite3_open_v2(path.c_str(), &opened, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nullptr);
    db_.reset(opened);
    if (result != SQLITE_OK) throw Failure("unavailable", "storage", sqlite3_errmsg(db_.get()));
    sqlite3_busy_timeout(db_.get(), 1000);
    {
        Statement version(db_.get(), "PRAGMA user_version");
        version.step();
        require(version.integer() == 0 || version.integer() == 1, "unsupported database schema version");
    }
    sql(db_.get(), "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;");
    sql(db_.get(), R"sql(
      CREATE TABLE IF NOT EXISTS scopes(scope TEXT PRIMARY KEY, current_gen INTEGER NOT NULL DEFAULT 0,
        watermark INTEGER NOT NULL DEFAULT 0, next_gen INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE IF NOT EXISTS generations(scope TEXT, generation INTEGER, parent INTEGER,
        watermark INTEGER, state TEXT, PRIMARY KEY(scope,generation));
      CREATE TABLE IF NOT EXISTS objects(uid INTEGER PRIMARY KEY, scope TEXT, id TEXT, revision INTEGER,
        payload TEXT, watermark INTEGER, origin INTEGER, UNIQUE(scope,id,revision,origin));
      CREATE INDEX IF NOT EXISTS canonical ON objects(scope,origin,id,revision);
      CREATE TABLE IF NOT EXISTS projections(uid INTEGER PRIMARY KEY REFERENCES objects(uid) ON DELETE CASCADE,
        lexical TEXT NOT NULL, concepts TEXT NOT NULL, model TEXT NOT NULL, dimension INTEGER NOT NULL, vector TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS members(scope TEXT, generation INTEGER, id TEXT, uid INTEGER REFERENCES objects(uid),
        PRIMARY KEY(scope,generation,id), FOREIGN KEY(scope,generation) REFERENCES generations(scope,generation));
      CREATE TABLE IF NOT EXISTS spaces(scope TEXT, model TEXT, dimension INTEGER, PRIMARY KEY(scope,model));
      CREATE TABLE IF NOT EXISTS retractions(scope TEXT, id TEXT, revision INTEGER, reason TEXT,
        PRIMARY KEY(scope,id,revision));
      CREATE TABLE IF NOT EXISTS leases(scope TEXT, id TEXT, generation INTEGER, expires REAL,
        PRIMARY KEY(scope,id), FOREIGN KEY(scope,generation) REFERENCES generations(scope,generation));
      CREATE TABLE IF NOT EXISTS operations(scope TEXT, id TEXT, operation TEXT, arguments TEXT, result TEXT,
        PRIMARY KEY(scope,id));
      PRAGMA user_version=1;
    )sql");
}
Json Engine::metadata(const std::string& scope) {
    Statement row(db_.get(), "SELECT current_gen,watermark FROM scopes WHERE scope=?");
    row.bind(1, scope);
    if (row.step()) return {{"generation", row.integer()}, {"watermark", row.integer(1)}};
    return {{"generation", 0}, {"watermark", 0}};
}
Json Engine::handle(const Json& request) {
    Json request_id = nullptr;
    try {
        fields(request, {"protocol_version", "request_id", "operation", "scope", "arguments"});
        request_id = string_value(request["request_id"]);
        require(request["protocol_version"] == "memory/1", "unsupported protocol version");
        auto scope = string_value(request["scope"]), op = string_value(request["operation"]);
        const auto& args = request["arguments"];
        require(args.is_object(), "arguments must be an object");
        Transaction transaction(db_.get());
        bool mutation = op == "ingest" || op == "begin_generation" || op == "stage" || op == "publish"
                     || op == "lease" || op == "retract" || op == "prune";
        Json result;
        std::string operation_id;
        if (mutation) {
            require(args.contains("operation_id"), "operation_id is required");
            operation_id = string_value(args["operation_id"]);
            Statement prior(db_.get(), "SELECT operation,arguments,result FROM operations WHERE scope=? AND id=?");
            prior.bind(1, scope); prior.bind(2, operation_id);
            if (prior.step()) {
                if (prior.text() != op || prior.text(1) != args.dump())
                    throw Failure("conflict", "idempotency", "operation key reused with different content");
                result = Json::parse(prior.text(2));
            }
        }
        if (result.is_null()) {
            result = dispatch(scope, op, args);
            if (mutation) {
                Statement receipt(db_.get(), "INSERT INTO operations VALUES(?,?,?,?,?)");
                receipt.bind(1, scope); receipt.bind(2, operation_id); receipt.bind(3, op);
                receipt.bind(4, args.dump()); receipt.bind(5, result.dump()); receipt.step();
            }
        }
        auto meta = metadata(scope);
        Json response = {{"protocol_version", "memory/1"}, {"request_id", request_id}, {"status", "ok"},
                         {"data", result}, {"error", nullptr}, {"generation", meta["generation"]},
                         {"watermark", meta["watermark"]}};
        if (result.contains("_partial")) {
            response["status"] = "partial";
            response["error"] = result["_partial"];
            response["data"].erase("_partial");
        }
        transaction.commit();
        return response;
    } catch (const Failure& error) {
        return failure_response(request_id, error.status, error.code, error.what());
    } catch (const std::exception& error) {
        return failure_response(request_id, "invalid_request", "validation", error.what());
    }
}
void Engine::check_embedding(const std::string& scope, const Json& record) {
    if (!record.contains("embedding")) return;
    const auto& embedding = record["embedding"];
    Statement space(db_.get(), "SELECT dimension FROM spaces WHERE scope=? AND model=?");
    space.bind(1, scope); space.bind(2, embedding["model"].get<std::string>());
    auto dimension = static_cast<long long>(embedding["vector"].size());
    if (space.step()) {
        if (space.integer() != dimension) throw Failure("conflict", "embedding_space", "model dimension changed");
    } else {
        Statement insert(db_.get(), "INSERT INTO spaces VALUES(?,?,?)");
        insert.bind(1, scope); insert.bind(2, embedding["model"].get<std::string>()); insert.bind(3, dimension); insert.step();
    }
}
long long Engine::insert_record(const std::string& scope, Json record, long long revision,
                               long long watermark, long long origin) {
    check_embedding(scope, record);
    record["scope"] = scope; record["revision"] = revision; record["watermark"] = watermark;
    if (!record.contains("valid_to")) record["valid_to"] = nullptr;
    if (record.contains("embedding")) record["embedding"]["dimension"] = record["embedding"]["vector"].size();
    Statement insert(db_.get(), "INSERT INTO objects(scope,id,revision,payload,watermark,origin) VALUES(?,?,?,?,?,?)");
    insert.bind(1, scope); insert.bind(2, record["id"].get<std::string>()); insert.bind(3, revision);
    insert.bind(4, record.dump()); insert.bind(5, watermark); insert.bind(6, origin); insert.step();
    auto uid = sqlite3_last_insert_rowid(db_.get());
    Json lexical = tokens(record["content"].get<std::string>());
    Statement project(db_.get(), "INSERT INTO projections VALUES(?,?,?,?,?,?)");
    project.bind(1, uid); project.bind(2, lexical.dump()); project.bind(3, record["concepts"].dump());
    project.bind(4, record.contains("embedding") ? record["embedding"]["model"].get<std::string>() : "");
    project.bind(5, record.contains("embedding") ? static_cast<long long>(record["embedding"]["vector"].size()) : 0LL);
    project.bind(6, record.contains("embedding") ? record["embedding"]["vector"].dump() : "[]"); project.step();
    return uid;
}
Json Engine::materialize(long long uid, const std::string& scope) {
    Statement object(db_.get(), "SELECT payload,id,revision FROM objects WHERE uid=? AND scope=?");
    object.bind(1, uid); object.bind(2, scope);
    if (!object.step()) throw Failure("invalid_request", "not_found", "memory record not found");
    auto record = Json::parse(object.text());
    Statement revoked(db_.get(), "SELECT 1 FROM retractions WHERE scope=? AND id=? AND revision IN(0,?) LIMIT 1");
    revoked.bind(1, scope); revoked.bind(2, object.text(1)); revoked.bind(3, object.integer(2));
    record["retracted"] = revoked.step();
    return record;
}
Json Engine::dispatch(const std::string& scope, const std::string& op, const Json& args) {
    if (op == "ping") {
        fields(args, {});
        Statement count(db_.get(), "SELECT count(*) FROM generations WHERE scope=? AND state='staging'");
        count.bind(1, scope); count.step();
        return {{"ready", true}, {"pending_generations", count.integer()}, {"current_generation", metadata(scope)["generation"]}};
    }
    if (op == "query") return query(scope, args);
    if (op == "get_revision") {
        fields(args, {"id"}, {"revision"});
        auto id = string_value(args["id"]);
        auto revision = args.contains("revision") ? integer(args["revision"], 1) : 0;
        Statement object(db_.get(), "SELECT uid FROM objects WHERE scope=? AND id=? AND origin=0 AND (?=0 OR revision=?) ORDER BY revision DESC LIMIT 1");
        object.bind(1, scope); object.bind(2, id); object.bind(3, revision); object.bind(4, revision);
        if (!object.step()) throw Failure("invalid_request", "not_found", "memory revision not found");
        return {{"record", materialize(object.integer(), scope)}};
    }
    if (op == "release") {
        fields(args, {"lease_id"});
        Statement remove(db_.get(), "DELETE FROM leases WHERE scope=? AND id=?");
        remove.bind(1, scope); remove.bind(2, string_value(args["lease_id"])); remove.step();
        return {{"released", sqlite3_changes(db_.get()) > 0}};
    }
    static const std::set<std::string> mutations{"ingest", "begin_generation", "stage", "publish", "lease", "retract", "prune"};
    require(mutations.contains(op), "unknown operation");
    Statement ensure(db_.get(), "INSERT OR IGNORE INTO scopes(scope) VALUES(?)");
    ensure.bind(1, scope); ensure.step();
    auto meta = metadata(scope);
    long long current = meta["generation"], watermark = meta["watermark"];
    if (op == "prune") {
        fields(args, {"operation_id", "generations"});
        const auto& requested = args["generations"];
        require(requested.is_array() && !requested.empty() && requested.size() <= 100, "invalid pruning batch");
        std::set<long long> seen;
        Json pruned = Json::array(), protected_ids = Json::array();
        for (const auto& value : requested) {
            auto generation = integer(value, 1);
            require(seen.insert(generation).second, "duplicate pruning generation");
            Statement exists(db_.get(), "SELECT 1 FROM generations WHERE scope=? AND generation=?");
            exists.bind(1, scope); exists.bind(2, generation);
            Statement readers(db_.get(), "SELECT 1 FROM leases WHERE scope=? AND generation=? AND expires>? LIMIT 1");
            readers.bind(1, scope); readers.bind(2, generation); readers.bind(3, now());
            std::string reason;
            if (!exists.step()) reason = "not_found";
            else if (generation == current) reason = "current";
            else if (readers.step()) reason = "leased";
            if (!reason.empty()) {
                protected_ids.push_back({{"generation", generation}, {"reason", reason}});
                continue;
            }
            Statement unpin(db_.get(), "DELETE FROM leases WHERE scope=? AND generation=?");
            unpin.bind(1, scope); unpin.bind(2, generation); unpin.step();
            Statement remove(db_.get(), "DELETE FROM members WHERE scope=? AND generation=?");
            remove.bind(1, scope); remove.bind(2, generation); remove.step();
            Statement retired(db_.get(), "UPDATE generations SET state='retired' WHERE scope=? AND generation=?");
            retired.bind(1, scope); retired.bind(2, generation); retired.step();
            pruned.push_back(generation);
        }
        Statement unused(db_.get(), R"sql(DELETE FROM objects WHERE scope=? AND origin>0
            AND NOT EXISTS(SELECT 1 FROM members m WHERE m.uid=objects.uid))sql");
        unused.bind(1, scope); unused.step();
        return {{"pruned", pruned}, {"protected", protected_ids}, {"derived_removed", sqlite3_changes(db_.get())}};
    }
    if (op == "ingest") {
        fields(args, {"operation_id", "expected_revision", "record"});
        record_valid(args["record"]);
        auto expected = integer(args["expected_revision"]);
        Statement latest(db_.get(), "SELECT coalesce(max(revision),0) FROM objects WHERE scope=? AND id=? AND origin=0");
        latest.bind(1, scope); latest.bind(2, args["record"]["id"].get<std::string>()); latest.step();
        if (latest.integer() != expected) throw Failure("conflict", "revision", "stale expected revision");
        Statement advance(db_.get(), "UPDATE scopes SET watermark=watermark+1 WHERE scope=?");
        advance.bind(1, scope); advance.step();
        auto uid = insert_record(scope, args["record"], expected + 1, watermark + 1, 0);
        return {{"record", materialize(uid, scope)}};
    }
    if (op == "retract") {
        fields(args, {"operation_id", "id", "reason"}, {"revision"});
        auto id = string_value(args["id"]), reason = string_value(args["reason"], 4096);
        auto revision = args.contains("revision") ? integer(args["revision"], 1) : 0;
        Statement insert(db_.get(), "INSERT INTO retractions VALUES(?,?,?,?) ON CONFLICT(scope,id,revision) DO UPDATE SET reason=excluded.reason");
        insert.bind(1, scope); insert.bind(2, id); insert.bind(3, revision); insert.bind(4, reason); insert.step();
        return {{"id", id}, {"revision", revision}, {"retracted", true}};
    }
    if (op == "begin_generation") {
        fields(args, {"operation_id", "expected_parent"}, {"source_watermark"});
        auto parent = integer(args["expected_parent"]);
        if (parent != current) throw Failure("conflict", "parent", "stale generation parent");
        auto source = args.contains("source_watermark") ? integer(args["source_watermark"]) : watermark;
        long long minimum = 0;
        if (current) {
            Statement previous(db_.get(), "SELECT watermark FROM generations WHERE scope=? AND generation=?");
            previous.bind(1, scope); previous.bind(2, current); previous.step(); minimum = previous.integer();
        }
        require(source <= watermark && source >= minimum, "source watermark outside available range");
        Statement next(db_.get(), "SELECT next_gen FROM scopes WHERE scope=?");
        next.bind(1, scope); next.step(); auto generation = next.integer();
        Statement insert(db_.get(), "INSERT INTO generations VALUES(?,?,?,?,'staging')");
        insert.bind(1, scope); insert.bind(2, generation); insert.bind(3, parent); insert.bind(4, source); insert.step();
        Statement advance(db_.get(), "UPDATE scopes SET next_gen=next_gen+1 WHERE scope=?");
        advance.bind(1, scope); advance.step();
        Statement copy(db_.get(), R"sql(INSERT INTO members SELECT ?,?,id,uid FROM objects o
          WHERE scope=? AND origin=0 AND watermark<=? AND revision=(SELECT max(revision)
          FROM objects x WHERE x.scope=o.scope AND x.id=o.id AND x.origin=0 AND x.watermark<=?))sql");
        copy.bind(1, scope); copy.bind(2, generation); copy.bind(3, scope); copy.bind(4, source); copy.bind(5, source); copy.step();
        Statement derived(db_.get(), R"sql(INSERT OR IGNORE INTO members SELECT ?,?,m.id,m.uid FROM members m JOIN objects o ON o.uid=m.uid
          WHERE m.scope=? AND m.generation=? AND o.origin>0)sql");
        derived.bind(1, scope); derived.bind(2, generation); derived.bind(3, scope); derived.bind(4, parent); derived.step();
        return {{"generation", generation}, {"parent", parent}, {"source_watermark", source}, {"state", "staging"}};
    }
    if (op == "lease") {
        fields(args, {"operation_id", "lease_id"}, {"generation", "ttl_seconds"});
        auto generation = args.contains("generation") ? integer(args["generation"], 1) : current;
        auto ttl = args.contains("ttl_seconds") ? integer(args["ttl_seconds"], 1, 3600) : 300;
        auto id = string_value(args["lease_id"]);
        Statement snapshot(db_.get(), "SELECT watermark FROM generations WHERE scope=? AND generation=? AND state='published'");
        snapshot.bind(1, scope); snapshot.bind(2, generation);
        if (!snapshot.step()) throw Failure("invalid_request", "generation_state", "generation is not published");
        Statement old(db_.get(), "SELECT 1 FROM leases WHERE scope=? AND id=?");
        old.bind(1, scope); old.bind(2, id);
        if (old.step()) throw Failure("conflict", "lease", "lease ID already exists");
        double expires = now() + ttl;
        Statement insert(db_.get(), "INSERT INTO leases VALUES(?,?,?,?)");
        insert.bind(1, scope); insert.bind(2, id); insert.bind(3, generation); insert.bind(4, expires); insert.step();
        return {{"lease_id", id}, {"generation", generation}, {"source_watermark", snapshot.integer()}, {"expires_at", expires}};
    }
    bool stage = op == "stage";
    if (stage) fields(args, {"operation_id", "generation", "records"});
    else fields(args, {"operation_id", "generation", "expected_parent"});
    auto generation = integer(args["generation"], 1);
    Statement snapshot(db_.get(), "SELECT parent,watermark,state FROM generations WHERE scope=? AND generation=?");
    snapshot.bind(1, scope); snapshot.bind(2, generation);
    if (!snapshot.step() || snapshot.text(2) != "staging")
        throw Failure("invalid_request", "generation_state", "generation is not staging");
    if (stage) {
        const auto& records = args["records"];
        require(records.is_array() && !records.empty() && records.size() <= 100, "invalid staging batch");
        std::set<std::string> seen;
        for (const auto& record : records) {
            record_valid(record);
            auto id = record["id"].get<std::string>();
            require(seen.insert(id).second, "duplicate staged ID");
            Statement existing(db_.get(), "SELECT o.origin FROM members m JOIN objects o ON o.uid=m.uid WHERE m.scope=? AND m.generation=? AND m.id=?");
            existing.bind(1, scope); existing.bind(2, generation); existing.bind(3, id);
            if (existing.step() && existing.integer() == 0)
                throw Failure("conflict", "revision", "cannot replace canonical record in generation");
            Statement remove(db_.get(), "DELETE FROM members WHERE scope=? AND generation=? AND id=?");
            remove.bind(1, scope); remove.bind(2, generation); remove.bind(3, id); remove.step();
            Statement purge(db_.get(), "DELETE FROM objects WHERE scope=? AND origin=? AND id=?");
            purge.bind(1, scope); purge.bind(2, generation); purge.bind(3, id); purge.step();
            auto uid = insert_record(scope, record, 1, snapshot.integer(1), generation);
            Statement add(db_.get(), "INSERT INTO members VALUES(?,?,?,?)");
            add.bind(1, scope); add.bind(2, generation); add.bind(3, id); add.bind(4, uid); add.step();
        }
        return {{"generation", generation}, {"staged", records.size()}};
    }
    auto expected = integer(args["expected_parent"]);
    if (expected != current || snapshot.integer() != current)
        throw Failure("conflict", "parent", "publication parent changed");
    Statement incomplete(db_.get(), R"sql(SELECT count(*) FROM members m LEFT JOIN projections p ON p.uid=m.uid
      WHERE m.scope=? AND m.generation=? AND p.uid IS NULL)sql");
    incomplete.bind(1, scope); incomplete.bind(2, generation); incomplete.step();
    if (incomplete.integer()) throw Failure("unavailable", "storage", "incomplete generation projection");
    Statement publish(db_.get(), "UPDATE generations SET state='published' WHERE scope=? AND generation=?");
    publish.bind(1, scope); publish.bind(2, generation); publish.step();
    Statement pointer(db_.get(), "UPDATE scopes SET current_gen=? WHERE scope=? AND current_gen=?");
    pointer.bind(1, generation); pointer.bind(2, scope); pointer.bind(3, expected); pointer.step();
    return {{"generation", generation}, {"source_watermark", snapshot.integer(1)}, {"state", "published"}};
}

Json Engine::query(const std::string& scope, const Json& args) {
    fields(args, {}, {"text", "concepts", "embedding", "at", "limit", "lease_id"});
    std::set<std::string> lexical, concepts;
    if (args.contains("text")) lexical = tokens(string_value(args["text"], 32768, true));
    if (args.contains("concepts")) {
        concepts_valid(args["concepts"]);
        concepts = args["concepts"].get<std::set<std::string>>();
    }
    bool vector = args.contains("embedding"), mismatch = false;
    std::string model;
    if (vector) {
        embedding_valid(args["embedding"]); model = args["embedding"]["model"].get<std::string>();
        Statement space(db_.get(), "SELECT dimension FROM spaces WHERE scope=? AND model=?");
        space.bind(1, scope); space.bind(2, model);
        mismatch = !space.step() || space.integer() != static_cast<long long>(args["embedding"]["vector"].size());
    }
    double at = args.contains("at") ? number(args["at"]) : now();
    require(at >= 0, "negative query time");
    auto limit = args.contains("limit") ? integer(args["limit"], 1, 100) : 20;
    auto meta = metadata(scope);
    long long generation = meta["generation"], source = meta["watermark"];
    bool leased = args.contains("lease_id");
    if (leased) {
        Statement lease(db_.get(), R"sql(SELECT l.generation,l.expires,g.watermark FROM leases l JOIN generations g
          ON g.scope=l.scope AND g.generation=l.generation WHERE l.scope=? AND l.id=?)sql");
        lease.bind(1, scope); lease.bind(2, string_value(args["lease_id"]));
        if (!lease.step() || lease.real(1) <= now()) throw Failure("invalid_request", "lease", "lease is missing or expired");
        generation = lease.integer(); source = lease.integer(2);
    }
    std::string candidate_sql = leased ?
      "SELECT o.uid,o.id,p.lexical,p.concepts,p.model,p.dimension,p.vector FROM members m JOIN objects o ON m.uid=o.uid JOIN projections p ON p.uid=o.uid WHERE m.scope=? AND m.generation=? ORDER BY o.id LIMIT 10001" :
      R"sql(SELECT o.uid,o.id,p.lexical,p.concepts,p.model,p.dimension,p.vector FROM objects o JOIN projections p ON p.uid=o.uid
        WHERE o.scope=? AND ((o.origin=0 AND o.revision=(SELECT max(revision) FROM objects x WHERE x.scope=o.scope AND x.id=o.id AND x.origin=0))
        OR (o.origin>0 AND o.uid IN(SELECT uid FROM members WHERE scope=o.scope AND generation=?)
        AND NOT EXISTS(SELECT 1 FROM objects x WHERE x.scope=o.scope AND x.id=o.id AND x.origin=0))) ORDER BY o.id LIMIT 10001)sql";
    Statement candidates(db_.get(), candidate_sql);
    candidates.bind(1, scope); candidates.bind(2, generation);
    Json scored = Json::array();
    unsigned scanned = 0;
    bool truncated = false;
    while (candidates.step()) {
        if (++scanned > 10000) { truncated = true; break; }
        auto record = materialize(candidates.integer(), scope);
        if (record["retracted"].get<bool>() || at < number(record["valid_from"])
            || (!record["valid_to"].is_null() && at >= number(record["valid_to"]))) continue;
        double ls = overlap(lexical, Json::parse(candidates.text(2)).get<std::set<std::string>>());
        double cs = overlap(concepts, Json::parse(candidates.text(3)).get<std::set<std::string>>());
        double vs = 0;
        if (vector && !candidates.text(4).empty()) {
            if (candidates.text(4) == model && candidates.integer(5) == static_cast<long long>(args["embedding"]["vector"].size()))
                vs = cosine(args["embedding"]["vector"], Json::parse(candidates.text(6)));
            else mismatch = true;
        }
        double score = ls + cs + vs;
        if ((!lexical.empty() || !concepts.empty() || vector) && score <= 0) continue;
        scored.push_back({{"record", record}, {"score", score}, {"lexical_score", ls}, {"concept_score", cs}, {"vector_score", vs}});
    }
    std::sort(scored.begin(), scored.end(), [](const Json& a, const Json& b) {
        if (a["score"] != b["score"]) return a["score"].get<double>() > b["score"].get<double>();
        return a["record"]["id"].get<std::string>() < b["record"]["id"].get<std::string>();
    });
    if (scored.size() > static_cast<std::size_t>(limit)) scored.erase(scored.begin() + limit, scored.end());
    Json result = {{"records", scored}, {"view", leased ? "snapshot" : "live"}, {"generation", generation},
                   {"source_watermark", source}, {"truncated", truncated}};
    if (mismatch || truncated) result["_partial"] = {{"code", truncated ? "candidate_limit" : "embedding_space"},
                                                    {"message", truncated ? "candidate scan bound exceeded" : "incompatible embedding spaces skipped"}};
    return result;
}
}
