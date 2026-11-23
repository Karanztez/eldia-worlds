#pragma once

#include <endstone/level/dimension.h>
#include <endstone/level/level.h>

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "sigscan.hpp"

namespace eldia::native {

struct DimensionSignatureTarget {
    std::string name;
    std::string pattern_linux;
    std::string pattern_win;
    std::uintptr_t known_rva_linux = 0;
    std::uintptr_t known_rva_win = 0;
    std::optional<std::uintptr_t> resolved_va;
};

struct CustomDimensionResult {
    bool success = false;
    int dimension_id = -1;
    std::string message;
};

class DimensionHookManager {
public:
    DimensionHookManager();

    bool initialize();
    std::vector<DimensionSignatureTarget> run_scan();

    CustomDimensionResult create_custom_dimension(
        endstone::Level *endstone_level,
        std::string_view name,
        std::string_view world_type
    );

    [[nodiscard]] const std::optional<ModuleRegion> &module_region() const {
        return module_region_;
    }

    [[nodiscard]] const std::vector<DimensionSignatureTarget> &targets() const {
        return targets_;
    }

private:
    std::optional<ModuleRegion> module_region_;
    std::vector<DimensionSignatureTarget> targets_;
    std::vector<std::array<std::uint64_t, 4>> weak_ref_slots_;
};

}  // namespace eldia::native
