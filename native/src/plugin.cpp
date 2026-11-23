#include <endstone/command/command.h>
#include <endstone/command/command_sender.h>
#include <endstone/level/dimension.h>
#include <endstone/level/level.h>
#include <endstone/plugin/plugin.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "dimension_hook.hpp"
#include "sigscan.hpp"

class EldiaWorldsNativePlugin : public endstone::Plugin {
public:
    void onLoad() override {
        getLogger().info("Initializing eldia_worlds_native...");
    }

    void onEnable() override {
        if (!hook_manager_.initialize()) {
            getLogger().warning("Could not locate bedrock_server executable module in memory.");
            return;
        }
        const auto &mod = *hook_manager_.module_region();
        char buf[192]{};
        std::snprintf(
            buf,
            sizeof(buf),
            "Attached to server module: base=0x%llx .text=0x%llx (size=0x%zx)",
            static_cast<unsigned long long>(mod.base),
            static_cast<unsigned long long>(mod.text_base),
            mod.text_size
        );
        getLogger().info(buf);
    }

    void onDisable() override {
        getLogger().info("eldia_worlds_native disabled.");
    }

    bool onCommand(
        endstone::CommandSender &sender,
        const endstone::Command &command,
        const std::vector<std::string> &args
    ) override {
        if (command.getName() != "nativeworld") {
            return false;
        }

        const std::string sub = args.empty() ? "status" : args[0];
        if (sub == "status") {
            const auto &mod = hook_manager_.module_region();
            if (!mod.has_value()) {
                sender.sendMessage("Server module not resolved.");
                return true;
            }
            char buf[160]{};
            std::snprintf(
                buf,
                sizeof(buf),
                "Server base=0x%llx .text=0x%llx size=0x%zx",
                static_cast<unsigned long long>(mod->base),
                static_cast<unsigned long long>(mod->text_base),
                mod->text_size
            );
            sender.sendMessage(buf);
            return true;
        }

        if (sub == "scan") {
            const auto results = hook_manager_.run_scan();
            for (const auto &item : results) {
                char line[256]{};
                if (item.resolved_va.has_value()) {
                    std::snprintf(
                        line,
                        sizeof(line),
                        "[OK] %s -> 0x%llx",
                        item.name.c_str(),
                        static_cast<unsigned long long>(*item.resolved_va)
                    );
                } else {
                    std::snprintf(
                        line,
                        sizeof(line),
                        "[PENDING_SIG] %s (pattern empty)",
                        item.name.c_str()
                    );
                }
                sender.sendMessage(line);
            }
            return true;
        }

        if (sub == "create") {
            if (args.size() < 2) {
                sender.sendMessage("Usage: /nativeworld create <name> [void|flat|normal]");
                return true;
            }
            const std::string &dim_name = args[1];
            const std::string world_type = args.size() >= 3 ? args[2] : "void";
            const auto res = hook_manager_.create_custom_dimension(
                getServer().getLevel(),
                dim_name,
                world_type
            );
            if (res.success) {
                getLogger().info(res.message);
                sender.sendMessage("[OK] " + res.message);
            } else {
                getLogger().warning(res.message);
                sender.sendMessage("[ERROR] " + res.message);
            }
            return true;
        }

        if (sub == "list") {
            auto *level = getServer().getLevel();
            if (level == nullptr) {
                sender.sendMessage("Server level is not loaded.");
                return true;
            }
            sender.sendMessage("=== Native Level Dimensions ===");
            for (auto *dim : level->getDimensions()) {
                if (dim == nullptr) {
                    continue;
                }
                sender.sendMessage(
                    " - " + dim->getName() + " (type=" + std::to_string(static_cast<int>(dim->getType())) + ")"
                );
            }
            return true;
        }

        if (sub == "probe" && args.size() >= 2) {
            const auto &mod = hook_manager_.module_region();
            if (!mod.has_value()) {
                sender.sendMessage("Server module not resolved.");
                return true;
            }
            const auto rva = static_cast<std::uintptr_t>(std::strtoull(args[1].c_str(), nullptr, 16));
            const auto va = mod->base + rva;
            const auto bytes = eldia::native::dump_bytes(va, 16);
            sender.sendMessage("Bytes at RVA: " + bytes);
            return true;
        }

        sender.sendMessage("Usage: /nativeworld <status|scan|list|create|probe> [arg1] [arg2]");
        return true;
    }

private:
    eldia::native::DimensionHookManager hook_manager_;
};

ENDSTONE_PLUGIN("eldia_worlds_native", "0.2.0", EldiaWorldsNativePlugin) {
    description = "Native C++ DimensionManager custom dimension creator for Endstone";
    authors = {"Eldia"};

    command("nativeworld")
        .description("Create and inspect native bedrock_server CustomDimensions")
        .usages(
            "/nativeworld",
            "/nativeworld [status|scan|list|create|probe] [arg1: str] [arg2: str]"
        )
        .permissions("eldia_worlds_native.command.nativeworld");

    permission("eldia_worlds_native.command.nativeworld")
        .description("Allows using /nativeworld")
        .default_(endstone::PermissionDefault::Operator);
}
