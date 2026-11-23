#include "sigscan.hpp"

#include <cctype>
#include <cstdio>
#include <cstring>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <link.h>
#endif

namespace eldia::native {

std::optional<ModuleRegion> get_server_module() {
#ifdef _WIN32
    HMODULE mod = GetModuleHandleA(nullptr);
    if (!mod) {
        return std::nullopt;
    }
    const auto base = reinterpret_cast<std::uintptr_t>(mod);
    const auto *dos = reinterpret_cast<const IMAGE_DOS_HEADER *>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
        return std::nullopt;
    }
    const auto *nt = reinterpret_cast<const IMAGE_NT_HEADERS64 *>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) {
        return std::nullopt;
    }
    const auto *sec = IMAGE_FIRST_SECTION(nt);
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections; ++i) {
        if (std::strncmp(reinterpret_cast<const char *>(sec[i].Name), ".text", 5) == 0) {
            return ModuleRegion{
                base,
                base + sec[i].VirtualAddress,
                sec[i].Misc.VirtualSize,
            };
        }
    }
    return ModuleRegion{base, base, nt->OptionalHeader.SizeOfImage};
#else
    struct CallbackData {
        std::optional<ModuleRegion> result;
    } ctx;

    dl_iterate_phdr(
        [](struct dl_phdr_info *info, std::size_t, void *user_data) -> int {
            auto *out = static_cast<CallbackData *>(user_data);
            // Main executable has empty dlpi_name.
            if (info->dlpi_name == nullptr || info->dlpi_name[0] == '\0') {
                for (ElfW(Half) i = 0; i < info->dlpi_phnum; ++i) {
                    const auto &ph = info->dlpi_phdr[i];
                    if (ph.p_type == PT_LOAD && (ph.p_flags & PF_X) != 0) {
                        out->result = ModuleRegion{
                            static_cast<std::uintptr_t>(info->dlpi_addr),
                            static_cast<std::uintptr_t>(info->dlpi_addr + ph.p_vaddr),
                            static_cast<std::size_t>(ph.p_memsz),
                        };
                        return 1;
                    }
                }
            }
            return 0;
        },
        &ctx
    );
    return ctx.result;
#endif
}

std::vector<PatternByte> parse_pattern(std::string_view pattern) {
    std::vector<PatternByte> out;
    std::size_t i = 0;
    while (i < pattern.size()) {
        while (i < pattern.size() && std::isspace(static_cast<unsigned char>(pattern[i]))) {
            ++i;
        }
        if (i >= pattern.size()) {
            break;
        }
        if (pattern[i] == '?') {
            out.push_back(PatternByte{0, true});
            while (i < pattern.size() && pattern[i] == '?') {
                ++i;
            }
        } else if (i + 1 < pattern.size()) {
            auto hex_val = [](char c) -> int {
                if (c >= '0' && c <= '9') return c - '0';
                if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
                if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
                return -1;
            };
            const int hi = hex_val(pattern[i]);
            const int lo = hex_val(pattern[i + 1]);
            if (hi >= 0 && lo >= 0) {
                out.push_back(PatternByte{static_cast<std::uint8_t>((hi << 4) | lo), false});
            }
            i += 2;
        } else {
            ++i;
        }
    }
    return out;
}

std::optional<std::uintptr_t> scan_region(
    std::uintptr_t start,
    std::size_t size,
    std::string_view pattern
) {
    const auto pat = parse_pattern(pattern);
    if (pat.empty() || size < pat.size()) {
        return std::nullopt;
    }
    const auto *mem = reinterpret_cast<const std::uint8_t *>(start);
    const std::size_t limit = size - pat.size();
    for (std::size_t offset = 0; offset <= limit; ++offset) {
        bool matched = true;
        for (std::size_t j = 0; j < pat.size(); ++j) {
            if (!pat[j].wildcard && mem[offset + j] != pat[j].value) {
                matched = false;
                break;
            }
        }
        if (matched) {
            return start + offset;
        }
    }
    return std::nullopt;
}

std::string dump_bytes(std::uintptr_t addr, std::size_t count) {
    const auto *mem = reinterpret_cast<const std::uint8_t *>(addr);
    std::string out;
    out.reserve(count * 3);
    char buf[4]{};
    for (std::size_t i = 0; i < count; ++i) {
        if (i > 0) {
            out.push_back(' ');
        }
        std::snprintf(buf, sizeof(buf), "%02X", mem[i]);
        out.append(buf);
    }
    return out;
}

}  // namespace eldia::native
