// RT64 renderer for the runtime. Adapted from Zelda64Recomp v1.2.2
// (src/main/rt64_render_context.cpp, GPL-3.0) without its texture packs, mods and UI hooks.
#include <algorithm>
#include <cstdio>
#include <cstdlib>

#define HLSL_CPU
#include "hle/rt64_application.h"

#include "ultramodern/ultramodern.hpp"
#include "ultramodern/config.hpp"

#include "ppl_render.h"
#include "ppl_trace.h"

namespace ur = ultramodern::renderer;

static uint8_t DMEM[0x1000];
static uint8_t IMEM[0x1000];

static unsigned int MI_INTR_REG = 0;
static unsigned int DPC_START_REG = 0;
static unsigned int DPC_END_REG = 0;
static unsigned int DPC_CURRENT_REG = 0;
static unsigned int DPC_STATUS_REG = 0;
static unsigned int DPC_CLOCK_REG = 0;
static unsigned int DPC_BUFBUSY_REG = 0;
static unsigned int DPC_PIPEBUSY_REG = 0;
static unsigned int DPC_TMEM_REG = 0;

static void dummy_check_interrupts() {}

static RT64::UserConfiguration::AspectRatio to_rt64(ur::AspectRatio option) {
    switch (option) {
        case ur::AspectRatio::Expand: return RT64::UserConfiguration::AspectRatio::Expand;
        case ur::AspectRatio::Manual: return RT64::UserConfiguration::AspectRatio::Manual;
        default:                      return RT64::UserConfiguration::AspectRatio::Original;
    }
}

static RT64::UserConfiguration::Antialiasing to_rt64(ur::Antialiasing option) {
    switch (option) {
        case ur::Antialiasing::MSAA2X: return RT64::UserConfiguration::Antialiasing::MSAA2X;
        case ur::Antialiasing::MSAA4X: return RT64::UserConfiguration::Antialiasing::MSAA4X;
        case ur::Antialiasing::MSAA8X: return RT64::UserConfiguration::Antialiasing::MSAA8X;
        default:                       return RT64::UserConfiguration::Antialiasing::None;
    }
}

static RT64::UserConfiguration::RefreshRate to_rt64(ur::RefreshRate option) {
    switch (option) {
        case ur::RefreshRate::Display: return RT64::UserConfiguration::RefreshRate::Display;
        case ur::RefreshRate::Manual:  return RT64::UserConfiguration::RefreshRate::Manual;
        default:                       return RT64::UserConfiguration::RefreshRate::Original;
    }
}

static RT64::UserConfiguration::InternalColorFormat to_rt64(ur::HighPrecisionFramebuffer option) {
    switch (option) {
        case ur::HighPrecisionFramebuffer::On:  return RT64::UserConfiguration::InternalColorFormat::High;
        case ur::HighPrecisionFramebuffer::Off: return RT64::UserConfiguration::InternalColorFormat::Standard;
        default:                                return RT64::UserConfiguration::InternalColorFormat::Automatic;
    }
}

static ppl::renderer::Filter2D filter_2d = ppl::renderer::Filter2D::Xbr;

void ppl::renderer::set_2d_filter(Filter2D filter) {
    filter_2d = filter;
}

// The game is all 2D, nearly all of it drawn unscaled (copy mode backgrounds, sprites at 1:1),
// and its big images are loaded in strips of a few lines (TMEM). Filtering each draw leaves a
// seam at every strip, as the filter can't reach the next strip's first line. So xbr, sharp
// and linear compose the frame at the N64's resolution, as the console does, and filter the
// whole picture when RT64 enlarges it to the window (the resolution option doesn't apply):
// xbr with xBR level 2, which rounds off the edges it finds (patches/rt64/0001), sharp with
// RT64's pixel scaling (nearest inside each pixel, a blend one window pixel wide at their
// edges, as sharp-bilinear), linear with plain bilinear. Nearest draws at the chosen
// resolution, the 2D included (RT64's default, ScaledOnly, would draw it at the N64's and
// enlarge it), with sharp pixels.
static void set_2d_user_config(RT64::Application* application) {
    using RT64::UserConfiguration;
    auto& user = application->userConfig;
    if (filter_2d == ppl::renderer::Filter2D::Nearest) {
        user.upscale2D = UserConfiguration::Upscale2D::All;
        user.filtering = UserConfiguration::Filtering::AntiAliasedPixelScaling;
        return;
    }
    user.resolution = UserConfiguration::Resolution::Manual;
    user.resolutionMultiplier = 1;
    user.downsampleMultiplier = 1;
    switch (filter_2d) {
        case ppl::renderer::Filter2D::Xbr:    user.filtering = UserConfiguration::Filtering::PixelArtXBR; break;
        case ppl::renderer::Filter2D::Linear: user.filtering = UserConfiguration::Filtering::Linear; break;
        default:                              user.filtering = UserConfiguration::Filtering::AntiAliasedPixelScaling; break;
    }
}

static void set_application_user_config(RT64::Application* application, const ur::GraphicsConfig& config) {
    switch (config.res_option) {
        default:
        case ur::Resolution::Auto:
            application->userConfig.resolution = RT64::UserConfiguration::Resolution::WindowIntegerScale;
            application->userConfig.downsampleMultiplier = 1;
            break;
        case ur::Resolution::Original:
            application->userConfig.resolution = RT64::UserConfiguration::Resolution::Manual;
            application->userConfig.resolutionMultiplier = std::max(config.ds_option, 1);
            application->userConfig.downsampleMultiplier = std::max(config.ds_option, 1);
            break;
        case ur::Resolution::Original2x:
            application->userConfig.resolution = RT64::UserConfiguration::Resolution::Manual;
            application->userConfig.resolutionMultiplier = 2.0 * std::max(config.ds_option, 1);
            application->userConfig.downsampleMultiplier = std::max(config.ds_option, 1);
            break;
    }

    switch (config.hr_option) {
        default:
        case ur::HUDRatioMode::Original:
            application->userConfig.extAspectRatio = RT64::UserConfiguration::AspectRatio::Original;
            break;
        case ur::HUDRatioMode::Clamp16x9:
            application->userConfig.extAspectRatio = RT64::UserConfiguration::AspectRatio::Manual;
            application->userConfig.extAspectTarget = 16.0 / 9.0;
            break;
        case ur::HUDRatioMode::Full:
            application->userConfig.extAspectRatio = RT64::UserConfiguration::AspectRatio::Expand;
            break;
    }

    application->userConfig.aspectRatio = to_rt64(config.ar_option);
    application->userConfig.antialiasing = to_rt64(config.msaa_option);
    application->userConfig.refreshRate = to_rt64(config.rr_option);
    application->userConfig.refreshRateTarget = config.rr_manual_value;
    application->userConfig.internalColorFormat = to_rt64(config.hpfb_option);
    application->userConfig.displayBuffering = RT64::UserConfiguration::DisplayBuffering::Triple;
    set_2d_user_config(application);
}

static ur::SetupResult map_setup_result(RT64::Application::SetupResult result) {
    switch (result) {
        case RT64::Application::SetupResult::Success:                  return ur::SetupResult::Success;
        case RT64::Application::SetupResult::DynamicLibrariesNotFound: return ur::SetupResult::DynamicLibrariesNotFound;
        case RT64::Application::SetupResult::InvalidGraphicsAPI:       return ur::SetupResult::InvalidGraphicsAPI;
        case RT64::Application::SetupResult::GraphicsAPINotFound:      return ur::SetupResult::GraphicsAPINotFound;
        case RT64::Application::SetupResult::GraphicsDeviceNotFound:   return ur::SetupResult::GraphicsDeviceNotFound;
    }
    fprintf(stderr, "Unhandled RT64::Application::SetupResult\n");
    std::exit(EXIT_FAILURE);
}

static ur::GraphicsApi map_graphics_api(RT64::UserConfiguration::GraphicsAPI api) {
    switch (api) {
        case RT64::UserConfiguration::GraphicsAPI::D3D12:  return ur::GraphicsApi::D3D12;
        case RT64::UserConfiguration::GraphicsAPI::Vulkan: return ur::GraphicsApi::Vulkan;
        case RT64::UserConfiguration::GraphicsAPI::Metal:  return ur::GraphicsApi::Metal;
        default:                                           return ur::GraphicsApi::Auto;
    }
}

ppl::renderer::RT64Context::RT64Context(uint8_t* rdram, ur::WindowHandle window_handle, bool developer_mode) {
    static unsigned char dummy_rom_header[0x40];

    RT64::Application::Core core{};
#if defined(_WIN32)
    core.window = window_handle.window;
#elif defined(__linux__) || defined(__ANDROID__)
    core.window = window_handle;
#elif defined(__APPLE__)
    core.window.window = window_handle.window;
    core.window.view = window_handle.view;
#endif
    core.checkInterrupts = dummy_check_interrupts;
    core.HEADER = dummy_rom_header;
    core.RDRAM = rdram;
    core.DMEM = DMEM;
    core.IMEM = IMEM;

    core.MI_INTR_REG = &MI_INTR_REG;
    core.DPC_START_REG = &DPC_START_REG;
    core.DPC_END_REG = &DPC_END_REG;
    core.DPC_CURRENT_REG = &DPC_CURRENT_REG;
    core.DPC_STATUS_REG = &DPC_STATUS_REG;
    core.DPC_CLOCK_REG = &DPC_CLOCK_REG;
    core.DPC_BUFBUSY_REG = &DPC_BUFBUSY_REG;
    core.DPC_PIPEBUSY_REG = &DPC_PIPEBUSY_REG;
    core.DPC_TMEM_REG = &DPC_TMEM_REG;

    ur::ViRegs* vi = ur::get_vi_regs();
    core.VI_STATUS_REG = &vi->VI_STATUS_REG;
    core.VI_ORIGIN_REG = &vi->VI_ORIGIN_REG;
    core.VI_WIDTH_REG = &vi->VI_WIDTH_REG;
    core.VI_INTR_REG = &vi->VI_INTR_REG;
    core.VI_V_CURRENT_LINE_REG = &vi->VI_V_CURRENT_LINE_REG;
    core.VI_TIMING_REG = &vi->VI_TIMING_REG;
    core.VI_V_SYNC_REG = &vi->VI_V_SYNC_REG;
    core.VI_H_SYNC_REG = &vi->VI_H_SYNC_REG;
    core.VI_LEAP_REG = &vi->VI_LEAP_REG;
    core.VI_H_START_REG = &vi->VI_H_START_REG;
    core.VI_V_START_REG = &vi->VI_V_START_REG;
    core.VI_V_BURST_REG = &vi->VI_V_BURST_REG;
    core.VI_X_SCALE_REG = &vi->VI_X_SCALE_REG;
    core.VI_Y_SCALE_REG = &vi->VI_Y_SCALE_REG;

    RT64::ApplicationConfiguration app_config;
    app_config.useConfigurationFile = false;
    app = std::make_unique<RT64::Application>(core, app_config);

    const ur::GraphicsConfig& config = ur::get_graphics_config();
    set_application_user_config(app.get(), config);
    app->userConfig.developerMode = developer_mode;
    switch (config.api_option) {
        case ur::GraphicsApi::D3D12:  app->userConfig.graphicsAPI = RT64::UserConfiguration::GraphicsAPI::D3D12; break;
        case ur::GraphicsApi::Vulkan: app->userConfig.graphicsAPI = RT64::UserConfiguration::GraphicsAPI::Vulkan; break;
        case ur::GraphicsApi::Metal:  app->userConfig.graphicsAPI = RT64::UserConfiguration::GraphicsAPI::Metal; break;
        default:                      app->userConfig.graphicsAPI = RT64::UserConfiguration::GraphicsAPI::Automatic; break;
    }

    uint32_t thread_id = 0;
#ifdef _WIN32
    thread_id = window_handle.thread_id;
#endif
    setup_result = map_setup_result(app->setup(thread_id));
    chosen_api = map_graphics_api(app->chosenGraphicsAPI);
    if (setup_result != ur::SetupResult::Success) {
        app = nullptr;
        return;
    }

    app->setFullScreen(config.wm_option == ur::WindowMode::Fullscreen);
}

ppl::renderer::RT64Context::~RT64Context() = default;

void ppl::renderer::RT64Context::send_dl(const OSTask* task) {
    ppl::trace::gfx_tasks++;
    if (const int remaining = ppl::trace::dump_remaining.load(); remaining > 0) {
        ppl::trace::dump_task(app->core.RDRAM, task, "_" + std::to_string(ppl::trace::dump_tasks - remaining));
        ppl::trace::dump_remaining--;
    }
    static const bool dump_offscreen = std::getenv("PPL_DUMP_OFFSCREEN") != nullptr;
    static int offscreen_dumped = 0;
    if (dump_offscreen && offscreen_dumped < ppl::trace::offscreen_dumps && ppl::trace::draws_offscreen(app->core.RDRAM, task)) {
        ppl::trace::dump_task(app->core.RDRAM, task, "_offscreen_" + std::to_string(offscreen_dumped++));
    }
    app->state->rsp->reset();
    app->interpreter->loadUCodeGBI(task->t.ucode & 0x3FFFFFF, task->t.ucode_data & 0x3FFFFFF, true);
    add_s2dex_commands(app->interpreter->hleGBI);
    app->processDisplayLists(app->core.RDRAM, task->t.data_ptr & 0x3FFFFFF, 0, true);
}

void ppl::renderer::RT64Context::update_screen() {
    app->updateScreen();
}

void ppl::renderer::RT64Context::shutdown() {
    if (app != nullptr) {
        app->end();
    }
}

bool ppl::renderer::RT64Context::update_config(const ur::GraphicsConfig& old_config, const ur::GraphicsConfig& new_config) {
    if (old_config == new_config) {
        return false;
    }
    if (new_config.wm_option != old_config.wm_option) {
        app->setFullScreen(new_config.wm_option == ur::WindowMode::Fullscreen);
    }
    set_application_user_config(app.get(), new_config);
    app->updateUserConfig(true);
    if (new_config.msaa_option != old_config.msaa_option) {
        app->updateMultisampling();
    }
    return true;
}

void ppl::renderer::RT64Context::enable_instant_present() {
    // Early presents bypass the inspector's pause (RT64 State::updateScreen), so keep the
    // default presentation in developer mode.
    if (app->userConfig.developerMode) {
        return;
    }
    app->enhancementConfig.presentation.mode = RT64::EnhancementConfiguration::Presentation::Mode::PresentEarly;
    app->updateEnhancementConfig();
}

uint32_t ppl::renderer::RT64Context::get_display_framerate() const {
    return app->presentQueue->ext.sharedResources->swapChainRate;
}

float ppl::renderer::RT64Context::get_resolution_scale() const {
    constexpr int reference_height = 240;
    switch (app->userConfig.resolution) {
        case RT64::UserConfiguration::Resolution::WindowIntegerScale:
            if (app->sharedQueueResources->swapChainHeight > 0) {
                return std::max(float((app->sharedQueueResources->swapChainHeight + reference_height - 1) / reference_height), 1.0f);
            }
            return 1.0f;
        case RT64::UserConfiguration::Resolution::Manual:
            return float(app->userConfig.resolutionMultiplier);
        default:
            return 1.0f;
    }
}

std::unique_ptr<ur::RendererContext> ppl::renderer::create_render_context(uint8_t* rdram, ur::WindowHandle window_handle, bool developer_mode) {
    return std::make_unique<RT64Context>(rdram, window_handle, developer_mode);
}
