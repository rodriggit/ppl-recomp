// config.ini: controls and graphics options (port/src/ppl_config.cpp).
#ifndef PPL_CONFIG_H
#define PPL_CONFIG_H

#include <filesystem>
#include <string>
#include <vector>

namespace ppl::config {
    // Reads config.ini next to the executable, or in fallback_folder if there is one there.
    // If neither exists, writes the defaults next to the executable (or in fallback_folder if
    // that folder isn't writable). Keys missing from the file take their default. Returns the
    // path of the file in use (empty if it couldn't be read or written).
    std::filesystem::path load(const std::filesystem::path& fallback_folder);

    // Value of `key` in [section] (both case-insensitive), lowercased and with surrounding
    // spaces removed (SDL's key and button names are case-insensitive).
    std::string get(const std::string& section, const std::string& key);

    // Comma-separated values of `key` in [section]; empty entries are dropped.
    std::vector<std::string> get_list(const std::string& section, const std::string& key);

    // A path as UTF-8 text, for messages.
    std::string path_text(const std::filesystem::path& path);

    // Problems found while interpreting the file, shown together once at startup.
    void warn(const std::string& message);
    const std::string& warnings();
}

#endif
