#pragma once

#include <cstdint>
#include <optional>
#include <string>
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

class DimensionHookManager {
public:
    DimensionHookManager();

    bool initialize();
    std::vector<DimensionSignatureTarget> run_scan();

    [[nodiscard]] const std::optional<ModuleRegion> &module_region() const {
        return module_region_;
    }

    [[nodiscard]] const std::vector<DimensionSignatureTarget> &targets() const {
        return targets_;
    }

private:
    std::optional<ModuleRegion> module_region_;
    std::vector<DimensionSignatureTarget> targets_;
};

}  // namespace eldia::native
