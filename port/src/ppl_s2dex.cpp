// S2DEX 1.x commands RT64 lacks or gets wrong at the pinned commit, registered into its
// command table by the port (rt64_context.cpp) instead of patching the shared checkout.
//
// G_OBJ_RECTANGLE (0x03): a non-rotated sprite (uObjSprite). The game draws the puzzle blocks
// and most of the match HUD with it; without it RT64 ignores the command.
//
// G_BG_COPY (0x02): RT64's version computes the TMEM line of an 8-bit image four times too
// long and halves tmemH for CI images into strips of half lines, so the strips overrun TMEM
// into the palette; it also ignores imageX/imageY. The game composes its HUD from CI8
// backgrounds (frame borders, the middle column), which came out garbled.
#include <algorithm>

#include "hle/rt64_state.h"
#include "gbi/rt64_gbi.h"
#include "gbi/rt64_gbi_s2dex.h"
#include "shared/rt64_f3d_defines.h"

#include "ppl_render.h"

namespace {
    constexpr uint8_t S2DEX_G_OBJ_RECTANGLE = 0x03;
    constexpr uint8_t G_OBJ_FLAG_FLIPS = 0x01, G_OBJ_FLAG_FLIPT = 0x10;
    constexpr uint32_t tmem_bytes = 0x1000;

    RT64::GBIFunction rt64_bg_copy = nullptr;   // RT64's own, for the cases bg_copy leaves to it

    // A big-endian halfword of an S2DEX structure. RDRAM holds 32-bit words in host order,
    // so it is the high half of its word at offset 0 and the low half at offset 2.
    uint16_t read_half(RT64::State* state, uint32_t address) {
        const uint32_t word = *reinterpret_cast<const uint32_t*>(state->fromRDRAM(address & ~3u));
        return (address & 2) ? (word & 0xFFFF) : (word >> 16);
    }

    struct ObjBg {
        uint16_t imageX;        // u10.5
        uint16_t imageW;        // u10.2
        int16_t frameX;         // s10.2
        uint16_t frameW;        // u10.2
        uint16_t imageY;
        uint16_t imageH;
        int16_t frameY;
        uint16_t frameH;
        uint32_t imagePtr;
        uint16_t imageLoad;
        uint8_t imageFmt, imageSiz;
        uint16_t imagePal;
    };

    ObjBg read_obj_bg(RT64::State* state, uint32_t address) {
        auto half = [&](uint32_t offset) { return read_half(state, address + offset); };
        return ObjBg{
            .imageX = half(0x00), .imageW = half(0x02), .frameX = int16_t(half(0x04)), .frameW = half(0x06),
            .imageY = half(0x08), .imageH = half(0x0A), .frameY = int16_t(half(0x0C)), .frameH = half(0x0E),
            .imagePtr = uint32_t(half(0x10)) << 16 | half(0x12), .imageLoad = half(0x14),
            .imageFmt = uint8_t(half(0x16) >> 8), .imageSiz = uint8_t(half(0x16)), .imagePal = half(0x18),
        };
    }

    // Copies the frame from the image one TMEM-sized strip of whole lines at a time: a
    // palette (CI) takes the upper half of TMEM. The frame starts at imageX/imageY in the
    // image; rows past the image's bottom aren't drawn (the microcode wraps them).
    void bg_copy(RT64::State* state, RT64::DisplayList** dl) {
        RT64::RDP* rdp = state->rdp.get();
        const ObjBg bg = read_obj_bg(state, state->rsp->fromSegmentedMasked((*dl)->w1));
        if (bg.imageSiz == G_IM_SIZ_32b || bg.imageLoad != S2DEX_G_BGLT_LOADTILE) {
            rt64_bg_copy(state, dl);
            return;
        }
        const uint32_t width = bg.imageW >> 2, height = bg.imageH >> 2;
        const uint32_t line_bytes = (width << bg.imageSiz) / 2;                 // 4, 8, 16 bits per texel
        const uint16_t line_words = uint16_t((line_bytes + 7) / 8);
        const uint32_t capacity = bg.imageFmt == G_IM_FMT_CI ? tmem_bytes / 2 : tmem_bytes;
        const uint32_t strip_lines = std::max<uint32_t>(capacity / (line_words * 8u), 1);
        const uint16_t lrs = uint16_t(bg.imageW - 4);
        const int16_t s = int16_t(bg.imageX);                                 // s10.5, as imageX
        const int16_t dsdx = 4 << 10, dtdy = 1 << 10;                         // copy mode: one texel per pixel

        state->rsp->setTextureImage(bg.imageFmt, bg.imageSiz, uint16_t(width), state->rsp->fromSegmented(bg.imagePtr));
        rdp->setTile(G_TX_LOADTILE, bg.imageFmt, bg.imageSiz, line_words, 0, uint8_t(bg.imagePal), 0, 0, 0, 0, 0, 0);
        rdp->setTile(G_TX_RENDERTILE, bg.imageFmt, bg.imageSiz, line_words, 0, uint8_t(bg.imagePal), 0, 0, 0, 0, 0, 0);

        // As RT64 does: when the image is a framebuffer RT64 rendered (or has a replacement),
        // draw the whole frame from it at once.
        uint64_t replacement_hash = 0;
        if (state->ext.enhancementConfig->s2dex.framebufferFastPath) {
            const uint16_t lrt = uint16_t(bg.imageH - 4);
            const bool replacement = rdp->loadTileReplacementCheck(G_TX_LOADTILE, 0, 0, lrs, lrt, bg.imageSiz, bg.imageFmt, bg.imageLoad, bg.imagePal, replacement_hash);
            if (replacement || rdp->loadTileCopyCheck(G_TX_LOADTILE, 0, 0, lrs, lrt)) {
                state->startSpriteCommand(replacement_hash);
                rdp->setTileReplacementHash(G_TX_RENDERTILE, replacement_hash);
                rdp->setTileSize(G_TX_RENDERTILE, 0, 0, lrs, lrt);
                rdp->loadTile(G_TX_LOADTILE, 0, 0, lrs, lrt);
                rdp->drawTexRect(bg.frameX, bg.frameY, bg.frameX + bg.frameW - 4, bg.frameY + bg.frameH - 4,
                    G_TX_RENDERTILE, s, int16_t(bg.imageY), dsdx, dtdy, false);
                rdp->clearTileReplacementHash(G_TX_RENDERTILE);
                state->endSpriteCommand();
                return;
            }
        }

        state->startSpriteCommand(replacement_hash);
        const uint32_t first = bg.imageY >> 5;
        const uint32_t rows = std::min<uint32_t>(bg.frameH >> 2, height > first ? height - first : 0);
        for (uint32_t y = 0; y < rows; y += strip_lines) {
            const uint32_t n = std::min(strip_lines, rows - y);
            const uint16_t ult = uint16_t((first + y) << 2);
            rdp->loadTile(G_TX_LOADTILE, 0, ult, lrs, uint16_t(ult + ((n - 1) << 2)));
            rdp->setTileSize(G_TX_RENDERTILE, 0, 0, lrs, uint16_t((n - 1) << 2));
            const int32_t uly = bg.frameY + int32_t(y << 2);
            rdp->drawTexRect(bg.frameX, uly, bg.frameX + bg.frameW - 4, uly + int32_t((n - 1) << 2),
                G_TX_RENDERTILE, s, 0, dsdx, dtdy, false);
        }
        state->endSpriteCommand();
    }

    // uObjSprite fields by byte offset (see read_half).
    struct ObjSprite {
        int16_t objX;           // s10.2
        uint16_t scaleW;        // u5.10
        uint16_t imageW;        // u10.5
        int16_t objY;
        uint16_t scaleH;
        uint16_t imageH;
        uint16_t imageStride;   // TMEM line, in 64-bit words
        uint16_t imageAdrs;     // TMEM address, in 64-bit words
        uint8_t imageFmt, imageSiz, imagePal, imageFlags;
    };

    ObjSprite read_obj_sprite(RT64::State* state, uint32_t address) {
        auto half = [&](uint32_t offset) { return read_half(state, address + offset); };
        return ObjSprite{
            .objX = int16_t(half(0x00)), .scaleW = half(0x02), .imageW = half(0x04),
            .objY = int16_t(half(0x08)), .scaleH = half(0x0A), .imageH = half(0x0C),
            .imageStride = half(0x10), .imageAdrs = half(0x12),
            .imageFmt = uint8_t(half(0x14) >> 8), .imageSiz = uint8_t(half(0x14)),
            .imagePal = uint8_t(half(0x16) >> 8), .imageFlags = uint8_t(half(0x16)),
        };
    }

    // As the microcode does it: the sprite's texture is already in TMEM (G_OBJ_LOADTXTR); set
    // the render tile to it, clamped, and draw a texture rectangle stepping scaleW/scaleH
    // texels per pixel, so the sprite covers imageW / scaleW pixels.
    void obj_rectangle(RT64::State* state, RT64::DisplayList** dl) {
        RT64::RDP* rdp = state->rdp.get();
        const ObjSprite sprite = read_obj_sprite(state, state->rsp->fromSegmentedMasked((*dl)->w1));
        if (sprite.scaleW == 0 || sprite.scaleH == 0) {
            return;
        }
        const int32_t w = std::max(sprite.imageW >> 5, 1), h = std::max(sprite.imageH >> 5, 1);
        rdp->setTile(G_TX_RENDERTILE, sprite.imageFmt, sprite.imageSiz, sprite.imageStride, sprite.imageAdrs,
            sprite.imagePal, G_TX_CLAMP | G_TX_NOMIRROR, G_TX_CLAMP | G_TX_NOMIRROR, G_TX_NOMASK, G_TX_NOMASK, G_TX_NOLOD, G_TX_NOLOD);
        rdp->setTileSize(G_TX_RENDERTILE, 0, 0, uint16_t((w - 1) << 2), uint16_t((h - 1) << 2));

        // Screen coordinates in 10.2; the size in pixels is image (u10.5) / scale (u5.10).
        const int32_t ulx = sprite.objX, uly = sprite.objY;
        const int32_t lrx = ulx + int32_t(uint32_t(sprite.imageW) * 128 / sprite.scaleW);
        const int32_t lry = uly + int32_t(uint32_t(sprite.imageH) * 128 / sprite.scaleH);

        // Texture start in s10.5 and step in s5.10; a flip starts at the last texel and steps back.
        int16_t uls = 0, ult = 0, dsdx = int16_t(sprite.scaleW), dtdy = int16_t(sprite.scaleH);
        if (sprite.imageFlags & G_OBJ_FLAG_FLIPS) {
            uls = int16_t((w << 5) - 1);
            dsdx = -dsdx;
        }
        if (sprite.imageFlags & G_OBJ_FLAG_FLIPT) {
            ult = int16_t((h << 5) - 1);
            dtdy = -dtdy;
        }

        state->startSpriteCommand(0);
        rdp->drawTexRect(ulx, uly, lrx, lry, G_TX_RENDERTILE, uls, ult, dsdx, dtdy, false);
        state->endSpriteCommand();
    }
}

void ppl::renderer::add_s2dex_commands(RT64::GBI* gbi) {
    if (gbi == nullptr || gbi->ucode != RT64::GBIUCode::S2DEX || gbi->map[S2DEX_G_BG_COPY] == &bg_copy) {
        return;
    }
    rt64_bg_copy = gbi->map[S2DEX_G_BG_COPY];
    gbi->map[S2DEX_G_BG_COPY] = &bg_copy;
    gbi->map[S2DEX_G_OBJ_RECTANGLE] = &obj_rectangle;
}
