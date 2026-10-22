// Mod files served to the game's file archive (port/src/ppl_mods.cpp).
#ifndef PPL_MODS_H
#define PPL_MODS_H

#include <filesystem>

namespace ppl::mods {
    // Reads [mods] from config.ini and, when a campaign is set, the files it lists from
    // <folder>/<campaign>/files.ini. Problems go to ppl::config::warn.
    void load(const std::filesystem::path& executable_folder);
}

#endif
