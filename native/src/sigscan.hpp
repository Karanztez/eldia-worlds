#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace eldia::native {

struct ModuleRegion {
    std::uintptr_t base = 0;
    std::uintptr_t text_base = 0;
    std::size_t text_size = 0;
};

struct PatternByte {
    std::uint8_t value = 0;
    bool wildcard = false;
};

/// Returns the main executable (bedrock_server) base and .text region.
std::optional<ModuleRegion> get_server_module();

/// Parses an IDA-style pattern such as "48 89 5C 24 ? 57 48 83 EC 20".
std::vector<PatternByte> parse_pattern(std::string_view pattern);

/// Scans [start, start + size) for the first match of an IDA-style pattern.
std::optional<std::uintptr_t> scan_region(
    std::uintptr_t start,
    std::size_t size,
    std::string_view pattern
);

/// Formats the first `count` bytes at `addr` as a space-separated hex string.
std::string dump_bytes(std::uintptr_t addr, std::size_t count);

}  // namespace eldia::native
