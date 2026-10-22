// config.ini: a small INI reader (sections, key = value, comment lines starting with ; or #)
// and the default file, which doubles as the documentation of every option. Adapted from
// mt64-recomp's mt_config.cpp.
#include <algorithm>
#include <cctype>
#include <fstream>
#include <map>
#include <sstream>

#ifdef _WIN32
#include "SDL.h"
#else
#include "SDL2/SDL.h"
#endif

#include "ppl_config.h"

static const char* const default_config = R"(; PPLRecomp settings. Lines starting with ; are comments.
; Delete this file to get the defaults back. Changes take effect on the next start.

[keyboard]
; The keyboard plays as controller 1. Key names as SDL writes them: A-Z, 0-9, Return,
; Space, Tab, Backspace, Escape, Up, Down, Left, Right, Left Shift, Right Shift,
; Left Ctrl, Right Ctrl, Left Alt, Keypad 0-9, F1-F12 (F9 and F11 are taken by the port).
; Several keys for one button: separate them with commas. Leave empty for none.
a = X
b = C
z = Z
l = A
r = S
start = Return
c_up = I
c_down = K
c_left = J
c_right = L
dpad_up = T
dpad_down = G
dpad_left = F
dpad_right = H
stick_up = Up
stick_down = Down
stick_left = Left
stick_right = Right

[gamepad]
; Gamepads play as controllers 1-4 in the order they are connected.
; Buttons (Xbox layout): a, b, x, y, back, guide, start, leftstick, rightstick,
; leftshoulder, rightshoulder, dpup, dpdown, dpleft, dpright.
; Triggers: lefttrigger, righttrigger.
; Stick directions: leftx-, leftx+, lefty-, lefty+, rightx-, rightx+, righty-, righty+
; (x- is left, y- is up).
a = a
b = x, b
z = lefttrigger
l = leftshoulder
r = rightshoulder, righttrigger
start = start
c_up = righty-
c_down = righty+
c_left = rightx-
c_right = rightx+
dpad_up = dpup
dpad_down = dpdown
dpad_left = dpleft
dpad_right = dpright
; Extra inputs that push the N64 stick all the way (e.g. dpup, dpdown...).
stick_up =
stick_down =
stick_left =
stick_right =
; Which analog stick is the N64 stick: left or right.
stick = left
; Stick travel ignored around the center, from 0 to 0.9.
deadzone = 0.15

[graphics]
; resolution: auto (as sharp as the window allows), original (the N64's own), 2x (twice that).
; Only with filter_2d = nearest (see below).
resolution = auto
; fullscreen: yes or no. F11 or Alt+Enter switches while playing.
fullscreen = no
; filter_2d: how the picture is enlarged to the window.
; xbr = drawn at the N64's resolution, then enlarged with xBR: the edges of the drawings
;   are rounded off instead of showing as steps, without blurring the rest.
; sharp = drawn at the N64's resolution, then enlarged with sharp pixels whose edges are
;   smoothed (as sharp-bilinear).
; linear = drawn at the N64's resolution, then smoothed as a whole (bilinear).
; nearest = drawn at the chosen resolution, with sharp square pixels.
; resolution only applies to nearest.
filter_2d = xbr
; api: auto, d3d12 (Windows) or vulkan.
api = auto

[mods]
; campaign: a folder inside the mods folder whose files.ini lists archive files to replace
; or add ("CBROCK.BIF = CFALKNER.BIF"). Empty: play the original game.
campaign =
; folder: where the campaign folders are; relative paths start at the executable's folder.
folder = mods
)";

namespace {
    std::map<std::string, std::map<std::string, std::string>> values;
    std::string warning_text;

    std::string trim(const std::string& s) {
        size_t start = s.find_first_not_of(" \t\r\n");
        size_t end = s.find_last_not_of(" \t\r\n");
        return start == std::string::npos ? std::string() : s.substr(start, end - start + 1);
    }

    std::string lower(std::string s) {
        std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(std::tolower(c)); });
        return s;
    }

    void parse(std::istream& in, const std::string& source) {
        std::string line, section;
        int number = 0;
        while (std::getline(in, line)) {
            number++;
            line = trim(line);
            if (line.empty() || line[0] == ';' || line[0] == '#') {
                continue;
            }
            if (line.front() == '[' && line.back() == ']') {
                section = lower(trim(line.substr(1, line.size() - 2)));
                continue;
            }
            size_t eq = line.find('=');
            if (eq == std::string::npos) {
                ppl::config::warn(source + " line " + std::to_string(number) + ": no '=' in \"" + line + "\"");
                continue;
            }
            values[section][lower(trim(line.substr(0, eq)))] = lower(trim(line.substr(eq + 1)));
        }
    }

    std::filesystem::path executable_folder() {
        char* base = SDL_GetBasePath();
        std::filesystem::path path = base != nullptr ? std::filesystem::u8path(base) : std::filesystem::current_path();
        SDL_free(base);
        return path;
    }

    bool write_defaults(const std::filesystem::path& path) {
        std::ofstream out(path);
        out << default_config;
        out.close();
        return static_cast<bool>(out);
    }
}

std::filesystem::path ppl::config::load(const std::filesystem::path& fallback_folder) {
    std::istringstream defaults(default_config);
    parse(defaults, "defaults");

    const std::filesystem::path beside_exe = executable_folder() / "config.ini";
    const std::filesystem::path in_fallback = fallback_folder / "config.ini";
    std::filesystem::path path;
    std::error_code ec;
    if (std::filesystem::exists(beside_exe, ec)) {
        path = beside_exe;
    } else if (std::filesystem::exists(in_fallback, ec)) {
        path = in_fallback;
    } else if (write_defaults(beside_exe)) {
        return beside_exe;
    } else if (write_defaults(in_fallback)) {
        return in_fallback;
    } else {
        warn("could not write " + path_text(beside_exe) + " nor " + path_text(in_fallback) + "; using the defaults");
        return {};
    }

    std::ifstream in(path);
    if (!in) {
        warn("could not read " + path_text(path) + "; using the defaults");
        return {};
    }
    parse(in, "config.ini");
    return path;
}

std::string ppl::config::get(const std::string& section, const std::string& key) {
    auto s = values.find(lower(section));
    if (s == values.end()) {
        return {};
    }
    auto v = s->second.find(lower(key));
    return v == s->second.end() ? std::string() : v->second;
}

std::vector<std::string> ppl::config::get_list(const std::string& section, const std::string& key) {
    std::vector<std::string> list;
    std::stringstream in(get(section, key));
    std::string item;
    while (std::getline(in, item, ',')) {
        item = trim(item);
        if (!item.empty()) {
            list.push_back(item);
        }
    }
    return list;
}

std::string ppl::config::path_text(const std::filesystem::path& path) {
    const std::u8string text = path.u8string();
    return std::string(text.begin(), text.end());
}

void ppl::config::warn(const std::string& message) {
    warning_text += message + "\n";
}

const std::string& ppl::config::warnings() {
    return warning_text;
}
