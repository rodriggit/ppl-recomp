// PPLRecomp: registers the game with the runtime and wires SDL2 (window, audio, input) and
// RT64 (rendering). Minimal by design: no menu, the ROM path is given on the command line
// once (or the ROM is dropped onto the executable) and stored by the runtime; controls and
// graphics options come from config.ini (ppl_config.cpp).
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>
#include <vector>

#define SDL_MAIN_HANDLED
#ifdef _WIN32
#include "SDL.h"
#include "SDL_syswm.h"
#else
#include "SDL2/SDL.h"
#include "SDL2/SDL_syswm.h"
#endif

#include "ultramodern/ultramodern.hpp"
#include "ultramodern/config.hpp"
#include "librecomp/game.hpp"
#include "librecomp/rsp.hpp"
#include "xxHash/xxh3.h"

#include "ppl_config.h"
#include "ppl_mods.h"
#include "ppl_render.h"
#include "ppl_trace.h"

extern "C" void recomp_entrypoint(uint8_t* rdram, recomp_context* ctx);
gpr get_entrypoint_address();
void ppl_register_overlays();
extern RspUcodeFunc aspMain;
extern std::atomic<uint64_t> ppl_vi_count;   // ppl_hooks.cpp

// XXH3-64 of the big-endian (.z64) ROM: USA release, NPNE rev 0
// (SHA-1 8173866fc8c7652abd44c48efcab85441c6806a1). A mismatching ROM prints its hash.
constexpr uint64_t PPL_ROM_HASH = 0xB6D572FF14BE7D30;
static const std::u8string game_id = u8"ppl.n64.us.1.0";
static const char* const app_name = "PPLRecomp";

static SDL_Window* window = nullptr;

static void message_box(const char* msg) {
    fprintf(stderr, "%s\n", msg);
    SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, app_name, msg, window);
}

// ---------------------------------------------------------------------------------------
// Input. SDL is only touched on the main thread (update_gfx); the game thread reads a
// snapshot. Port 1 = keyboard + first gamepad, ports 2-4 = the other gamepads.

namespace btn {
    constexpr uint16_t A = 0x8000, B = 0x4000, Z = 0x2000, START = 0x1000;
    constexpr uint16_t DU = 0x0800, DD = 0x0400, DL = 0x0200, DR = 0x0100;
    constexpr uint16_t L = 0x0020, R = 0x0010;
    constexpr uint16_t CU = 0x0008, CD = 0x0004, CL = 0x0002, CR = 0x0001;
}

constexpr int num_ports = 4;
constexpr float stick_range = 85.0f / 127.0f;   // full deflection of an original stick
constexpr float axis_press = 0.5f;              // a stick direction or trigger counts as pressed past this

// Controls from config.ini ([keyboard], [gamepad]): one binding per N64 button or stick direction.
enum StickDir { STICK_UP, STICK_DOWN, STICK_LEFT, STICK_RIGHT, STICK_NONE };

struct PadInput {
    bool is_axis;
    int index;      // SDL_GameControllerButton or SDL_GameControllerAxis
    float sign;     // axes: +1 or -1
};

struct Binding {
    const char* name;
    uint16_t mask;
    StickDir stick;
    std::vector<SDL_Scancode> keys;
    std::vector<PadInput> pad;
};

static std::array<Binding, 18> bindings{ {
    { "a", btn::A, STICK_NONE }, { "b", btn::B, STICK_NONE }, { "z", btn::Z, STICK_NONE },
    { "l", btn::L, STICK_NONE }, { "r", btn::R, STICK_NONE }, { "start", btn::START, STICK_NONE },
    { "c_up", btn::CU, STICK_NONE }, { "c_down", btn::CD, STICK_NONE },
    { "c_left", btn::CL, STICK_NONE }, { "c_right", btn::CR, STICK_NONE },
    { "dpad_up", btn::DU, STICK_NONE }, { "dpad_down", btn::DD, STICK_NONE },
    { "dpad_left", btn::DL, STICK_NONE }, { "dpad_right", btn::DR, STICK_NONE },
    { "stick_up", 0, STICK_UP }, { "stick_down", 0, STICK_DOWN },
    { "stick_left", 0, STICK_LEFT }, { "stick_right", 0, STICK_RIGHT },
} };

static SDL_GameControllerAxis pad_stick_x = SDL_CONTROLLER_AXIS_LEFTX;
static SDL_GameControllerAxis pad_stick_y = SDL_CONTROLLER_AXIS_LEFTY;
static float stick_deadzone = 0.15f;

static bool parse_yes_no(const std::string& section, const char* key, bool fallback) {
    const std::string v = ppl::config::get(section, key);
    if (v == "yes" || v == "true" || v == "1" || v == "on") return true;
    if (v == "no" || v == "false" || v == "0" || v == "off") return false;
    ppl::config::warn("[" + section + "] " + key + ": expected yes or no, got \"" + v + "\"");
    return fallback;
}

static bool parse_pad_input(const std::string& name, PadInput& out) {
    std::string base = name;
    float sign = 1.0f;
    if (!base.empty() && (base.back() == '+' || base.back() == '-')) {
        sign = base.back() == '-' ? -1.0f : 1.0f;
        base.pop_back();
    }
    const SDL_GameControllerButton button = SDL_GameControllerGetButtonFromString(base.c_str());
    if (button != SDL_CONTROLLER_BUTTON_INVALID && base.size() == name.size()) {
        out = { false, int(button), 1.0f };
        return true;
    }
    const SDL_GameControllerAxis axis = SDL_GameControllerGetAxisFromString(base.c_str());
    const bool trigger = axis == SDL_CONTROLLER_AXIS_TRIGGERLEFT || axis == SDL_CONTROLLER_AXIS_TRIGGERRIGHT;
    // Sticks need a direction; triggers take none.
    if (axis != SDL_CONTROLLER_AXIS_INVALID && (trigger == (base.size() == name.size()))) {
        out = { true, int(axis), sign };
        return true;
    }
    return false;
}

static void load_controls() {
    for (Binding& binding : bindings) {
        for (const std::string& key : ppl::config::get_list("keyboard", binding.name)) {
            const SDL_Scancode code = SDL_GetScancodeFromName(key.c_str());
            if (code == SDL_SCANCODE_UNKNOWN) {
                ppl::config::warn(std::string("[keyboard] ") + binding.name + ": unknown key \"" + key + "\"");
            } else {
                binding.keys.push_back(code);
            }
        }
        for (const std::string& input : ppl::config::get_list("gamepad", binding.name)) {
            PadInput pad;
            if (parse_pad_input(input, pad)) {
                binding.pad.push_back(pad);
            } else {
                ppl::config::warn(std::string("[gamepad] ") + binding.name + ": unknown input \"" + input + "\"");
            }
        }
    }
    const std::string stick = ppl::config::get("gamepad", "stick");
    if (stick == "right") {
        pad_stick_x = SDL_CONTROLLER_AXIS_RIGHTX;
        pad_stick_y = SDL_CONTROLLER_AXIS_RIGHTY;
    } else if (stick != "left") {
        ppl::config::warn("[gamepad] stick: expected left or right, got \"" + stick + "\"");
    }
    const std::string deadzone = ppl::config::get("gamepad", "deadzone");
    try {
        stick_deadzone = std::clamp(std::stof(deadzone), 0.0f, 0.9f);
    } catch (const std::exception&) {
        ppl::config::warn("[gamepad] deadzone: expected a number, got \"" + deadzone + "\"");
    }
}

struct PadState {
    bool connected = false;
    uint16_t buttons = 0;
    float x = 0.0f, y = 0.0f;
};

static std::mutex input_mutex;
static std::array<PadState, num_ports> pads;
static std::array<SDL_GameController*, num_ports> gamepads{};

static void open_gamepad(int device_index) {
    for (auto& slot : gamepads) {
        if (slot == nullptr) {
            slot = SDL_GameControllerOpen(device_index);
            return;
        }
    }
}

static void close_gamepad(SDL_JoystickID id) {
    for (auto& slot : gamepads) {
        if (slot != nullptr && SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(slot)) == id) {
            SDL_GameControllerClose(slot);
            slot = nullptr;
        }
    }
}

static float axis(SDL_GameController* pad, SDL_GameControllerAxis a) {
    float v = SDL_GameControllerGetAxis(pad, a) / 32767.0f;
    return std::abs(v) < stick_deadzone ? 0.0f : std::clamp(v, -1.0f, 1.0f);
}

static bool pad_input_pressed(SDL_GameController* pad, const PadInput& in) {
    if (!in.is_axis) {
        return SDL_GameControllerGetButton(pad, SDL_GameControllerButton(in.index)) != 0;
    }
    return SDL_GameControllerGetAxis(pad, SDL_GameControllerAxis(in.index)) / 32767.0f * in.sign > axis_press;
}

// Digital stick directions (keys, or pad inputs bound to stick directions) push the stick
// all the way; diagonals are scaled to the same distance.
static void apply_digital_stick(const std::array<bool, 4>& dirs, PadState& s) {
    float x = float(dirs[STICK_RIGHT]) - float(dirs[STICK_LEFT]);
    float y = float(dirs[STICK_UP]) - float(dirs[STICK_DOWN]);
    if (x != 0.0f && y != 0.0f) {
        x *= 0.7071f;
        y *= 0.7071f;
    }
    if (x != 0.0f || y != 0.0f) {
        s.x = x * stick_range;
        s.y = y * stick_range;
    }
}

static void read_gamepad(SDL_GameController* pad, PadState& s) {
    std::array<bool, 4> dirs{};
    for (const Binding& binding : bindings) {
        for (const PadInput& in : binding.pad) {
            if (pad_input_pressed(pad, in)) {
                s.buttons |= binding.mask;
                if (binding.stick != STICK_NONE) dirs[binding.stick] = true;
            }
        }
    }
    float x = axis(pad, pad_stick_x), y = -axis(pad, pad_stick_y);
    if (x != 0.0f || y != 0.0f) {
        s.x = x * stick_range;
        s.y = y * stick_range;
    }
    apply_digital_stick(dirs, s);
}

// The keyboard plays on port 1, together with the first gamepad.
static void read_keyboard(PadState& s) {
    const Uint8* k = SDL_GetKeyboardState(nullptr);
    std::array<bool, 4> dirs{};
    for (const Binding& binding : bindings) {
        for (SDL_Scancode key : binding.keys) {
            if (k[key]) {
                s.buttons |= binding.mask;
                if (binding.stick != STICK_NONE) dirs[binding.stick] = true;
            }
        }
    }
    apply_digital_stick(dirs, s);
}

static void update_input() {
    std::array<PadState, num_ports> next{};
    next[0].connected = true;
    read_keyboard(next[0]);
    for (int i = 0; i < num_ports; i++) {
        if (gamepads[i] == nullptr) {
            continue;
        }
        next[i].connected = true;
        read_gamepad(gamepads[i], next[i]);
    }
    std::lock_guard lock{ input_mutex };
    pads = next;
}

static bool get_input(int port, uint16_t* buttons, float* x, float* y) {
    if (port < 0 || port >= num_ports) {
        return false;
    }
    std::lock_guard lock{ input_mutex };
    if (!pads[port].connected) {
        return false;
    }
    *buttons = pads[port].buttons;
    *x = pads[port].x;
    *y = pads[port].y;
    return true;
}

// The game uses no controller pak (docs/phase1.md): only the controllers themselves.
static ultramodern::input::connected_device_info_t get_connected_device_info(int port) {
    using namespace ultramodern::input;
    std::lock_guard lock{ input_mutex };
    if (port < 0 || port >= num_ports || !pads[port].connected) {
        return { Device::None, Pak::None };
    }
    return { Device::Controller, Pak::None };
}

// ---------------------------------------------------------------------------------------
// Audio: the game's 16-bit stereo samples (channels swapped by the word-swapped RDRAM)
// go through an SDL_AudioStream that resamples to the device rate.

static SDL_AudioDeviceID audio_device = 0;
static SDL_AudioSpec audio_spec{};
static SDL_AudioStream* audio_stream = nullptr;
static uint32_t game_sample_rate = 32000;
static std::mutex audio_mutex;

static void set_frequency(uint32_t freq) {
    std::lock_guard lock{ audio_mutex };
    game_sample_rate = freq;
    if (audio_stream != nullptr) {
        SDL_FreeAudioStream(audio_stream);
    }
    audio_stream = SDL_NewAudioStream(AUDIO_S16SYS, 2, int(freq), audio_spec.format, audio_spec.channels, audio_spec.freq);
}

static size_t output_bytes_per_frame() {
    return audio_spec.channels * (SDL_AUDIO_BITSIZE(audio_spec.format) / 8);
}

static void queue_samples(int16_t* data, size_t sample_count) {
    std::lock_guard lock{ audio_mutex };
    if (audio_stream == nullptr) {
        return;
    }
    // Drop the chunk if more than 150 ms are already queued, so latency can't build up.
    uint32_t queued = SDL_GetQueuedAudioSize(audio_device);
    if (queued > output_bytes_per_frame() * audio_spec.freq * 15 / 100) {
        return;
    }
    static std::vector<int16_t> swapped;
    swapped.resize(sample_count);
    for (size_t i = 0; i + 1 < sample_count; i += 2) {
        swapped[i + 0] = data[i + 1];
        swapped[i + 1] = data[i + 0];
    }
    SDL_AudioStreamPut(audio_stream, swapped.data(), int(sample_count * sizeof(int16_t)));
    static std::vector<uint8_t> out;
    out.resize(size_t(SDL_AudioStreamAvailable(audio_stream)));
    int got = SDL_AudioStreamGet(audio_stream, out.data(), int(out.size()));
    if (got > 0) {
        SDL_QueueAudio(audio_device, out.data(), uint32_t(got));
    }
}

static size_t get_frames_remaining() {
    std::lock_guard lock{ audio_mutex };
    uint64_t out_frames = SDL_GetQueuedAudioSize(audio_device) / output_bytes_per_frame();
    uint64_t frames = out_frames * game_sample_rate / audio_spec.freq;
    // Report one video frame less, so the game generates audio slightly ahead and
    // the device doesn't run dry.
    uint64_t one_vi = game_sample_rate / 60;
    return size_t(frames > one_vi ? frames - one_vi : 0);
}

static void init_audio() {
    SDL_AudioSpec desired{};
    desired.freq = 48000;
    desired.format = AUDIO_F32SYS;
    desired.channels = 2;
    desired.samples = 0x100;
    audio_device = SDL_OpenAudioDevice(nullptr, 0, &desired, &audio_spec, 0);
    if (audio_device == 0) {
        fprintf(stderr, "Failed to open audio device: %s\n", SDL_GetError());
        audio_spec = desired;
        return;
    }
    set_frequency(game_sample_rate);
    SDL_PauseAudioDevice(audio_device, 0);
}

// ---------------------------------------------------------------------------------------
// Window and events

static ultramodern::gfx_callbacks_t::gfx_data_t create_gfx() {
    SDL_SetHint(SDL_HINT_WINDOWS_DPI_AWARENESS, "permonitorv2");
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER) != 0) {
        message_box(SDL_GetError());
        std::exit(EXIT_FAILURE);
    }
    return nullptr;
}

static ultramodern::renderer::WindowHandle create_window(ultramodern::gfx_callbacks_t::gfx_data_t) {
    uint32_t flags = SDL_WINDOW_RESIZABLE;
#if defined(__APPLE__)
    flags |= SDL_WINDOW_METAL;
#elif !defined(_WIN32)
    flags |= SDL_WINDOW_VULKAN;
#endif
    window = SDL_CreateWindow(app_name, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 1280, 960, flags);
    if (window == nullptr) {
        message_box(SDL_GetError());
        std::exit(EXIT_FAILURE);
    }
    SDL_SysWMinfo wm_info;
    SDL_VERSION(&wm_info.version);
    SDL_GetWindowWMInfo(window, &wm_info);
#if defined(_WIN32)
    return ultramodern::renderer::WindowHandle{ wm_info.info.win.window, GetCurrentThreadId() };
#elif defined(__linux__)
    return window;
#else
    static_assert(false, "Unsupported platform");
#endif
}

static void toggle_fullscreen() {
    auto config = ultramodern::renderer::get_graphics_config();
    config.wm_option = config.wm_option == ultramodern::renderer::WindowMode::Fullscreen
        ? ultramodern::renderer::WindowMode::Windowed
        : ultramodern::renderer::WindowMode::Fullscreen;
    ultramodern::renderer::set_graphics_config(config);
}

static void update_gfx(void*) {
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        switch (event.type) {
            case SDL_QUIT:
                ultramodern::quit();
                break;
            case SDL_CONTROLLERDEVICEADDED:
                open_gamepad(event.cdevice.which);
                break;
            case SDL_CONTROLLERDEVICEREMOVED:
                close_gamepad(event.cdevice.which);
                break;
            case SDL_KEYDOWN:
                if (event.key.keysym.scancode == SDL_SCANCODE_F11 ||
                    (event.key.keysym.scancode == SDL_SCANCODE_RETURN && (event.key.keysym.mod & KMOD_ALT))) {
                    toggle_fullscreen();
                }
                else if (event.key.keysym.scancode == SDL_SCANCODE_F9) {
                    ppl::trace::dump_remaining = ppl::trace::dump_tasks;
                }
                break;
        }
    }
    update_input();
}

// ---------------------------------------------------------------------------------------

// The VI thread only sets a placeholder video mode while the game hasn't started; once it
// has, it uses the game's mode. Start the game from the VI callback after a couple of VIs,
// so there is a mode before the game calls osViSetMode (the runtime's menu-less start).
// Every VI is also counted for osViGetCurrentLine (ppl_hooks.cpp).
static std::u8string pending_game_id;
static const bool trace_enabled = std::getenv("PPL_TRACE") != nullptr;

// PPL_TRACE=1: once per second, what the game did (ppl_trace.h) and what the VI shows.
static void print_trace() {
    const ultramodern::renderer::ViRegs* vi = ultramodern::renderer::get_vi_regs();
    fprintf(stderr, "[trace] gfx %u audio %u video %u retrace %u | VI status %08X origin %08X width %u\n",
        ppl::trace::gfx_tasks.exchange(0), ppl::trace::audio_tasks.exchange(0),
        ppl::trace::video_tasks.exchange(0), ppl::trace::retrace_waits.exchange(0),
        vi->VI_STATUS_REG, vi->VI_ORIGIN_REG, vi->VI_WIDTH_REG);
}

static void vi_callback() {
    static int vis = 0;
    const uint64_t count = ppl_vi_count.fetch_add(1) + 1;
    if (!pending_game_id.empty() && ++vis >= 2) {
        recomp::start_game(pending_game_id);
        pending_game_id.clear();
    }
    if (trace_enabled && count % 60 == 0) {
        print_trace();
    }
}

// Task type 7 is the HVQM2 video decoder (docs/phase0.md), not recompiled yet: it ends at
// once, so the videos aren't decoded but the game goes on.
constexpr uint32_t hvqm2_task_type = 7;

static RspExitReason skip_task(uint8_t*, uint32_t) {
    static bool reported = false;
    if (!reported) {
        fprintf(stderr, "[ppl] HVQM2 video task skipped (microcode not recompiled yet)\n");
        reported = true;
    }
    return RspExitReason::Broke;
}

static RspUcodeFunc* get_rsp_microcode(const OSTask* task) {
    switch (task->t.type) {
        case M_AUDTASK:
            ppl::trace::audio_tasks++;
            return aspMain;
        case hvqm2_task_type: {
            static bool task_printed = false;
            if (trace_enabled && !task_printed) {
                task_printed = true;
                const OSTask_s& t = task->t;
                fprintf(stderr, "[trace] HVQM2 task: flags %X ucode %08X ucode_data %08X data %08X (%X) output %08X yield %08X\n",
                    t.flags, (uint32_t)t.ucode, (uint32_t)t.ucode_data, (uint32_t)t.data_ptr, t.data_size,
                    (uint32_t)t.output_buff, (uint32_t)t.yield_data_ptr);
            }
            ppl::trace::video_tasks++;
            return skip_task;
        }
    }
    fprintf(stderr, "Unknown RSP task type: %u\n", (unsigned)task->t.type);
    return nullptr;
}

// Hash of a ROM file in .z64 byte order (as the runtime computes it), or 0 if unreadable.
static uint64_t rom_file_hash(const std::filesystem::path& path) {
    std::ifstream file{ path, std::ios::binary };
    std::vector<uint8_t> data{ std::istreambuf_iterator<char>(file), {} };
    if (data.size() < 4) {
        return 0;
    }
    data.resize((data.size() + 3) & ~size_t(3));
    size_t swap = data[0] == 0x80 ? 0 : data[0] == 0x37 ? 1 : data[0] == 0x40 ? 3 : 0;
    if (swap != 0) {
        for (size_t i = 0; i < data.size(); i += 4) {
            uint8_t w[4] = { data[i], data[i + 1], data[i + 2], data[i + 3] };
            for (size_t j = 0; j < 4; j++) {
                data[i + (j ^ swap)] = w[j];
            }
        }
    }
    return XXH3_64bits(data.data(), data.size());
}

static std::filesystem::path config_folder() {
    char* pref = SDL_GetPrefPath("ppl-recomp", app_name);
    std::filesystem::path path = pref != nullptr ? std::filesystem::u8path(pref) : std::filesystem::current_path();
    SDL_free(pref);
    return path;
}

static std::filesystem::path executable_folder() {
    char* base = SDL_GetBasePath();
    std::filesystem::path path = base != nullptr ? std::filesystem::u8path(base) : std::filesystem::current_path();
    SDL_free(base);
    return path;
}

// [graphics] of config.ini.
static ultramodern::renderer::GraphicsConfig load_graphics() {
    namespace ur = ultramodern::renderer;
    auto pick = [](const char* key, std::initializer_list<std::pair<const char*, int>> choices, int fallback) {
        const std::string v = ppl::config::get("graphics", key);
        std::string names;
        for (const auto& [name, value] : choices) {
            if (v == name) return value;
            names += names.empty() ? name : std::string(", ") + name;
        }
        ppl::config::warn(std::string("[graphics] ") + key + ": expected " + names + ", got \"" + v + "\"");
        return fallback;
    };

    ur::GraphicsConfig graphics{};
    // PPL_RT64_DEV=1: RT64's developer mode (F1 inspector, RDP warnings on the console)
    graphics.developer_mode = std::getenv("PPL_RT64_DEV") != nullptr;
    graphics.res_option = ur::Resolution(pick("resolution", {
        { "auto", int(ur::Resolution::Auto) }, { "original", int(ur::Resolution::Original) },
        { "2x", int(ur::Resolution::Original2x) } }, int(ur::Resolution::Auto)));
    graphics.wm_option = parse_yes_no("graphics", "fullscreen", false) ? ur::WindowMode::Fullscreen : ur::WindowMode::Windowed;
    // All 2D, drawn with rectangles: multisampling has nothing to smooth.
    graphics.msaa_option = ur::Antialiasing::None;
    graphics.api_option = ur::GraphicsApi(pick("api", {
        { "auto", int(ur::GraphicsApi::Auto) }, { "d3d12", int(ur::GraphicsApi::D3D12) },
        { "vulkan", int(ur::GraphicsApi::Vulkan) } }, int(ur::GraphicsApi::Auto)));
    using ppl::renderer::Filter2D;
    ppl::renderer::set_2d_filter(Filter2D(pick("filter_2d", {
        { "xbr", int(Filter2D::Xbr) }, { "sharp", int(Filter2D::Sharp) }, { "linear", int(Filter2D::Linear) },
        { "nearest", int(Filter2D::Nearest) } }, int(Filter2D::Xbr))));
    graphics.hr_option = ur::HUDRatioMode::Original;
    graphics.ar_option = ur::AspectRatio::Original;
    graphics.rr_option = ur::RefreshRate::Original;
    graphics.hpfb_option = ur::HighPrecisionFramebuffer::Auto;
    graphics.rr_manual_value = 60;
    graphics.ds_option = 1;
    return graphics;
}

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    setvbuf(stderr, nullptr, _IONBF, 0);

    recomp::Version version{};
    recomp::Version::from_string("0.1.0", version);

    SDL_InitSubSystem(SDL_INIT_AUDIO);
    init_audio();

    recomp::register_config_path(config_folder());
    const std::filesystem::path config_path = ppl::config::load(config_folder());
    fprintf(stderr, "[ppl] settings: %s\n", config_path.empty() ? "(defaults)" : ppl::config::path_text(config_path).c_str());
    load_controls();
    ppl::mods::load(executable_folder());

    recomp::GameEntry game{
        .rom_hash = PPL_ROM_HASH,
        .internal_name = "PUZZLE LEAGUE N64",   // the ROM header's name, used to tell a wrong version
        .game_id = game_id,
        .mod_game_id = "",                      // no mod support
        .save_type = recomp::SaveType::Flashram,
        .is_enabled = true,
        .entrypoint_address = get_entrypoint_address(),
        .entrypoint = recomp_entrypoint,
    };
    recomp::register_game(game);
    ppl_register_overlays();

    // Use the ROM given on the command line (stored for later runs), else the stored one.
    std::u8string id = game_id;
    if (argc > 1) {
        std::filesystem::path rom_path = std::filesystem::u8path(argv[1]);
        recomp::RomValidationError result = recomp::select_rom(rom_path, id);
        if (result != recomp::RomValidationError::Good) {
            char msg[512];
            snprintf(msg, sizeof(msg),
                "The ROM was not accepted (error %d).\n"
                "This port needs the USA release (NPNE, rev 0), in .z64, .n64 or .v64 format.\n"
                "ROM hash: 0x%016llX",
                (int)result, (unsigned long long)rom_file_hash(rom_path));
            message_box(msg);
            return EXIT_FAILURE;
        }
    }
    recomp::check_all_stored_roms();
    if (!recomp::is_rom_valid(id)) {
        message_box("No ROM yet. Run the program once with the path to your ROM (USA release),\n"
                    "or drop the ROM file onto the executable.");
        return EXIT_FAILURE;
    }

    ultramodern::renderer::set_graphics_config(load_graphics());
    if (!ppl::config::warnings().empty()) {
        const std::string msg = "Some settings in " + ppl::config::path_text(config_path) +
                                " were not understood and use their defaults:\n\n" + ppl::config::warnings();
        fprintf(stderr, "%s", msg.c_str());
        SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_WARNING, app_name, msg.c_str(), nullptr);
    }

    recomp::rsp::callbacks_t rsp_callbacks{
        .get_rsp_microcode = get_rsp_microcode,
    };
    ultramodern::renderer::callbacks_t renderer_callbacks{
        .create_render_context = ppl::renderer::create_render_context,
    };
    ultramodern::gfx_callbacks_t gfx_callbacks{
        .create_gfx = create_gfx,
        .create_window = create_window,
        .update_gfx = update_gfx,
    };
    ultramodern::audio_callbacks_t audio_callbacks{
        .queue_samples = queue_samples,
        .get_frames_remaining = get_frames_remaining,
        .set_frequency = set_frequency,
    };
    ultramodern::input::callbacks_t input_callbacks{
        .poll_input = nullptr,
        .get_input = get_input,
        .set_rumble = nullptr,
        .get_connected_device_info = get_connected_device_info,
    };
    ultramodern::events::callbacks_t events_callbacks{
        .vi_callback = vi_callback,
    };
    ultramodern::error_handling::callbacks_t error_callbacks{
        .message_box = message_box,
    };
    ultramodern::threads::callbacks_t threads_callbacks{};

    pending_game_id = id;
    recomp::start(version, {}, rsp_callbacks, renderer_callbacks, audio_callbacks, input_callbacks,
                  gfx_callbacks, events_callbacks, error_callbacks, threads_callbacks);
    return EXIT_SUCCESS;
}
