#include "dimension_hook.hpp"

#include <cstring>

namespace eldia::native {

namespace {

// Verified 64-byte layout of DimensionDefinitionGroup::DimensionDefinition
// from bedrock/linux/bedrock_server (0x0694cdf2..0x0694ce60 and 0x09519fec..0x0951a050).
struct alignas(8) BdsDimensionDefinition {
    std::int32_t min_y = -64;          // +0x00 (0xffffffc0)
    std::int32_t max_y = 384;          // +0x04 (0x00000180)
    std::int32_t sea_level = 128;      // +0x08 (0x00000080)
    std::uint8_t has_weather = 0;      // +0x0c
    std::uint8_t pad0[3]{0, 0, 0};     // +0x0d..0x0f
    std::int32_t generator_type = 5;   // +0x10 (5 = Void, 2 = Flat, 1 = Overworld)
    std::int32_t dimension_id = 3;     // +0x14 (written by registerCustomDimension)
    std::uint8_t uuid[16]{};           // +0x18..0x27 (mce::UUID::EMPTY)
    std::uint8_t biome_sso[24]{};      // +0x28..0x3f (libc++ SSO std::string "minecraft:ocean")
};
static_assert(sizeof(BdsDimensionDefinition) == 64, "BdsDimensionDefinition must be 64 bytes");

using RegisterCustomDimFn = std::uint64_t (*)(
    void *dimension_manager,
    const char *name_cstr,
    std::size_t name_len,
    BdsDimensionDefinition *def
);

using GetDimensionManagerFn = void *(*)(void *bds_level);
using GetOrCreateDimensionFn = void *(*)(void *weak_ref_out, void *bds_level, int dim_id);

bool matches_prefix(std::uintptr_t addr, const std::uint8_t *prefix, std::size_t len, const bool *wild) {
    const auto *mem = reinterpret_cast<const std::uint8_t *>(addr);
    for (std::size_t i = 0; i < len; ++i) {
        if (!wild[i] && mem[i] != prefix[i]) {
            return false;
        }
    }
    return true;
}

}  // namespace

DimensionHookManager::DimensionHookManager() {
    targets_ = {
        DimensionSignatureTarget{
            "DimensionManager::registerCustomDimension",
            "55 41 57 41 56 41 54 53 49 89 CC 48 89 D3 49 89 F6 49 89 FF 48 8B 7F 08 E8",
            "",
            0x0ce78dc0,
            0x0,
            std::nullopt,
        },
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
    };
}

bool DimensionHookManager::initialize() {
    module_region_ = get_server_module();
    if (module_region_.has_value()) {
        run_scan();
    }
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

CustomDimensionResult DimensionHookManager::create_custom_dimension(
    endstone::Level *endstone_level,
    std::string_view name,
    std::string_view world_type
) {
#ifdef _WIN32
    (void)endstone_level;
    (void)name;
    (void)world_type;
    return CustomDimensionResult{false, -1, "Native custom dimension registration is currently implemented for Linux x86_64 BDS."};
#else
    if (endstone_level == nullptr) {
        return CustomDimensionResult{false, -1, "Server level is not initialized yet."};
    }

    const std::string name_str(name);
    if (name_str.empty() || name_str.size() > 64) {
        return CustomDimensionResult{false, -1, "Dimension name must be 1..64 characters."};
    }

    if (auto *existing = endstone_level->getDimension(name_str); existing != nullptr) {
        return CustomDimensionResult{true, -1, "Dimension '" + name_str + "' is already loaded."};
    }

    if (targets_.empty() || !targets_[0].resolved_va.has_value()) {
        run_scan();
    }
    if (targets_.empty() || !targets_[0].resolved_va.has_value()) {
        return CustomDimensionResult{false, -1, "Could not resolve DimensionManager::registerCustomDimension signature in bedrock_server."};
    }

    // EndstoneLevel layout (src/endstone/core/level/level.h):
    // +0x00: vptr, +0x08: EndstoneServer&, +0x10: ::Level&
    auto *bds_level = *reinterpret_cast<void **>(reinterpret_cast<std::uintptr_t>(endstone_level) + 0x10);
    if (bds_level == nullptr) {
        return CustomDimensionResult{false, -1, "EndstoneLevel::level_ handle is null."};
    }

    auto *vtable = *reinterpret_cast<std::uintptr_t **>(bds_level);
    if (vtable == nullptr) {
        return CustomDimensionResult{false, -1, "BDS Level vtable is null."};
    }

    // Locate getDimensionManager (48 8B 87 ? ? 00 00 48 8B 40 18 C3)
    // and getOrCreateDimension (48 8B 86 ? ? 00 00 48 8B 70 18 E9) in ServerLevel vtable[4..16].
    const std::uint8_t dm_pat[12] = {0x48, 0x8B, 0x87, 0, 0, 0x00, 0x00, 0x48, 0x8B, 0x40, 0x18, 0xC3};
    const bool dm_wild[12] = {false, false, false, true, true, false, false, false, false, false, false, false};

    const std::uint8_t goc_pat[12] = {0x48, 0x8B, 0x86, 0, 0, 0x00, 0x00, 0x48, 0x8B, 0x70, 0x18, 0xE9};
    const bool goc_wild[12] = {false, false, false, true, true, false, false, false, false, false, false, false};

    std::uintptr_t get_dm_va = 0;
    std::uintptr_t get_or_create_va = 0;
    for (int slot = 4; slot <= 16; ++slot) {
        const std::uintptr_t fn = vtable[slot];
        if (fn == 0) {
            continue;
        }
        if (get_dm_va == 0 && matches_prefix(fn, dm_pat, 12, dm_wild)) {
            get_dm_va = fn;
        }
        if (get_or_create_va == 0 && matches_prefix(fn, goc_pat, 12, goc_wild)) {
            get_or_create_va = fn;
        }
    }
    if (get_dm_va == 0) {
        get_dm_va = vtable[11];
    }
    if (get_or_create_va == 0) {
        get_or_create_va = vtable[6];
    }

    auto get_dm_fn = reinterpret_cast<GetDimensionManagerFn>(get_dm_va);
    auto get_or_create_fn = reinterpret_cast<GetOrCreateDimensionFn>(get_or_create_va);
    auto register_fn = reinterpret_cast<RegisterCustomDimFn>(*targets_[0].resolved_va);

    void *dm = get_dm_fn(bds_level);
    if (dm == nullptr) {
        return CustomDimensionResult{false, -1, "Level::getDimensionManager() returned null."};
    }

    BdsDimensionDefinition def{};
    def.min_y = -64;
    def.max_y = 384;
    def.sea_level = 128;
    def.has_weather = 0;
    if (world_type == "flat") {
        def.generator_type = 2;
    } else if (world_type == "normal") {
        def.generator_type = 1;
    } else {
        def.generator_type = 5;  // VoidGenerator
    }
    def.dimension_id = 3;
    def.biome_sso[0] = 0x1e;  // libc++ short string length 15 << 1
    std::memcpy(&def.biome_sso[1], "minecraft:ocean", 16);

    const std::uint64_t opt_res = register_fn(dm, name_str.c_str(), name_str.size(), &def);
    const bool has_value = (opt_res >> 32) != 0;
    const int dim_id = static_cast<int>(opt_res & 0xFFFFFFFF);
    if (!has_value) {
        return CustomDimensionResult{
            false,
            -1,
            "DimensionManager::registerCustomDimension returned nullopt for '" + name_str + "'.",
        };
    }

    // Instantiate the Dimension via Level::getOrCreateDimension so EndstoneLevel's
    // OnNewDimensionCreatedConnector listener wraps it into server.level.dimensions.
    weak_ref_slots_.push_back({0, 0, 0, 0});
    auto &slot = weak_ref_slots_.back();
    get_or_create_fn(slot.data(), bds_level, dim_id);

    auto *created = endstone_level->getDimension(name_str);
    if (created == nullptr) {
        return CustomDimensionResult{
            false,
            dim_id,
            "Registered dimension ID " + std::to_string(dim_id) + " but EndstoneLevel did not wrap '" + name_str + "'.",
        };
    }

    return CustomDimensionResult{
        true,
        dim_id,
        "Created native CustomDimension '" + created->getName() + "' (id=" + std::to_string(dim_id) + ", generator=" + std::string(world_type) + ").",
    };
#endif
}

}  // namespace eldia::native
