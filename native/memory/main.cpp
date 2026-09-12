#include "engine.hpp"
#include <array>
#include <iostream>

int main(int argc, char** argv) {
    using namespace sturgis;
    if (argc != 3 || std::string(argv[1]) != "--db" || !*argv[2]) {
        std::cerr << "usage: sturgis-memory --db PATH\n";
        return 2;
    }
    try {
        Engine engine(argv[2]);
        for (;;) {
            std::array<unsigned char, 4> header{};
            std::cin.read(reinterpret_cast<char*>(header.data()), 4);
            if (std::cin.gcount() == 0 && std::cin.eof()) return 0;
            if (std::cin.gcount() != 4) throw std::runtime_error("truncated frame header");
            unsigned size = (unsigned(header[0]) << 24) | (unsigned(header[1]) << 16)
                          | (unsigned(header[2]) << 8) | unsigned(header[3]);
            if (size == 0 || size > max_frame) throw std::runtime_error("invalid frame length");
            std::string body(size, '\0');
            std::cin.read(body.data(), size);
            if (std::cin.gcount() != static_cast<std::streamsize>(size))
                throw std::runtime_error("truncated frame payload");
            Json response;
            try {
                response = engine.handle(strict_json(body));
            } catch (const std::exception& error) {
                response = failure_response(nullptr, "invalid_request", "validation", error.what());
            }
            // Parser diagnostics may quote malformed input bytes; keep the error frame valid UTF-8.
            auto output = response.dump(-1, ' ', false, Json::error_handler_t::replace);
            if (output.size() > max_frame) {
                auto bounded = failure_response(response["request_id"], "partial", "response_size",
                                                "response exceeds frame bound; reduce query limit");
                bounded["generation"] = response["generation"];
                bounded["watermark"] = response["watermark"];
                output = bounded.dump();
            }
            size = static_cast<unsigned>(output.size());
            header = {static_cast<unsigned char>(size >> 24), static_cast<unsigned char>(size >> 16),
                      static_cast<unsigned char>(size >> 8), static_cast<unsigned char>(size)};
            std::cout.write(reinterpret_cast<char*>(header.data()), 4);
            std::cout.write(output.data(), output.size());
            std::cout.flush();
            if (!std::cout) throw std::runtime_error("frame output failed");
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 2;
    }
}
