#pragma once

#include "vendor/json.hpp"
#include <memory>
#include <stdexcept>
#include <string>
#include <sqlite3.h>

namespace sturgis {
using Json = nlohmann::json;
inline constexpr unsigned max_frame = 1048576;

struct Failure : std::runtime_error {
    std::string status;
    std::string code;
    Failure(std::string status_, std::string code_, const std::string& message)
        : std::runtime_error(message), status(std::move(status_)), code(std::move(code_)) {}
};

Json strict_json(const std::string& text);
Json failure_response(const Json& request_id, const std::string& status,
                      const std::string& code, const std::string& message);

class Engine {
public:
    explicit Engine(const std::string& path);
    Json handle(const Json& request);
private:
    std::unique_ptr<sqlite3, decltype(&sqlite3_close)> db_{nullptr, sqlite3_close};
    Json dispatch(const std::string& scope, const std::string& op, const Json& args);
    Json metadata(const std::string& scope);
    void check_embedding(const std::string& scope, const Json& record);
    long long insert_record(const std::string& scope, Json record, long long revision,
                            long long watermark, long long origin);
    Json materialize(long long uid, const std::string& scope);
    Json query(const std::string& scope, const Json& args);
};
}
