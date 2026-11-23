#include "dimension_hook.hpp"

namespace eldia::native {

DimensionHookManager::DimensionHookManager() {
    // Verified targets from bedrock/linux/bedrock_server (1.26.66.1 ELF64)
    // and bedrock/window/bedrock_server.exe (1.26.66.1 PE64).
    targets_ = {
        DimensionSignatureTarget{
            "DimensionManager::_registerCustomDimensionWithFactory",
            "55 41 57 41 56 41 55 41 54 53 48 81 EC 18 01 00 00 48 83 FA F7 0F 83 ? ? ? ? 89 CD 48 89 D3",
            "",
            0x0ce79830,
            0x0,
            std::nullopt,
        },
        DimensionSignatureTarget{
            "VanillaDimensionFactory::registerDimensionTypes",
            "41 57 41 56 41 55 41 54 53 48 81 EC F0 00 00 00 49 89 FE 48 8D 35 ? ? ? ? 48 8D 5C 24 10",
            "",
            0x04b95500,
            0x0,
            std::nullopt,
        },
        DimensionSignatureTarget{
            "ServerLevel::ctor_or_init_xref",
            "",
            "",
            0x0,
            0x009505dd,
            std::nullopt,
        },
    };
}

bool DimensionHookManager::initialize() {
    module_region_ = get_server_module();
    return module_region_.has_value();
}

std::vector<DimensionSignatureTarget> DimensionHookManager::run_scan() {
    if (!module_region_.has_value()) {
        module_region_ = get_server_module();
    }
    if (!module_region_.has_value()) {
        return targets_;
    }

    for (auto &target : targets_) {
        target.resolved_va.reset();
#ifdef _WIN32
        const std::string &pat = target.pattern_win;
        const std::uintptr_t known_rva = target.known_rva_win;
#else
        const std::string &pat = target.pattern_linux;
        const std::uintptr_t known_rva = target.known_rva_linux;
#endif
        if (!pat.empty()) {
            target.resolved_va = scan_region(
                module_region_->text_base,
                module_region_->text_size,
                pat
            );
        } else if (known_rva != 0) {
            target.resolved_va = module_region_->base + known_rva;
        }
    }
    return targets_;
}

}  // namespace eldia::native
