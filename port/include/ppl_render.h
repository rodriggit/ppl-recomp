// RT64 renderer for the runtime (ultramodern::renderer::RendererContext).
#ifndef PPL_RENDER_H
#define PPL_RENDER_H

#include <memory>

#include "ultramodern/renderer_context.hpp"

namespace RT64 {
    struct Application;
    struct GBI;
}

namespace ppl::renderer {
    class RT64Context final : public ultramodern::renderer::RendererContext {
    public:
        RT64Context(uint8_t* rdram, ultramodern::renderer::WindowHandle window_handle, bool developer_mode);
        ~RT64Context() override;

        bool valid() override { return static_cast<bool>(app); }
        bool update_config(const ultramodern::renderer::GraphicsConfig& old_config,
                           const ultramodern::renderer::GraphicsConfig& new_config) override;
        void enable_instant_present() override;
        void send_dl(const OSTask* task) override;
        void update_screen() override;
        void shutdown() override;
        uint32_t get_display_framerate() const override;
        float get_resolution_scale() const override;

    private:
        std::unique_ptr<RT64::Application> app;
    };

    std::unique_ptr<ultramodern::renderer::RendererContext> create_render_context(
        uint8_t* rdram, ultramodern::renderer::WindowHandle window_handle, bool developer_mode);

    // config.ini's filter_2d (rt64_context.cpp). Applies to render contexts created after the call.
    enum class Filter2D { Xbr, Sharp, Linear, Nearest };
    void set_2d_filter(Filter2D filter);

    // Registers the S2DEX commands RT64 lacks into a loaded GBI (ppl_s2dex.cpp).
    void add_s2dex_commands(RT64::GBI* gbi);
}

#endif
