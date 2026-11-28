#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <unordered_set>
#include <vector>

#ifdef __ANDROID__
#include <android/log.h>
#include <jni.h>
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "BionzRaw", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "BionzRaw", __VA_ARGS__)
#else
#define LOGI(...)
#define LOGE(...)
#endif

namespace {

inline uint16_t read_u16(const uint8_t* p, bool le) {
    return le ? static_cast<uint16_t>(p[0] | (p[1] << 8))
              : static_cast<uint16_t>((p[0] << 8) | p[1]);
}

inline int16_t read_s16(const uint8_t* p, bool le) {
    return static_cast<int16_t>(read_u16(p, le));
}

inline uint32_t read_u32(const uint8_t* p, bool le) {
    return le ? (static_cast<uint32_t>(p[0]) |
                 (static_cast<uint32_t>(p[1]) << 8) |
                 (static_cast<uint32_t>(p[2]) << 16) |
                 (static_cast<uint32_t>(p[3]) << 24))
              : ((static_cast<uint32_t>(p[0]) << 24) |
                 (static_cast<uint32_t>(p[1]) << 16) |
                 (static_cast<uint32_t>(p[2]) << 8) |
                 static_cast<uint32_t>(p[3]));
}

inline uint32_t swap_be32(uint32_t v) {
    return ((v & 0x000000FFu) << 24) |
           ((v & 0x0000FF00u) << 8) |
           ((v & 0x00FF0000u) >> 8) |
           ((v & 0xFF000000u) >> 24);
}

void sony_decrypt(uint32_t* data, size_t len_words, uint32_t key) {
    uint32_t pad[128] = {0};
    for (int p = 0; p < 4; ++p) {
        key = key * 48828125u + 1u;
        pad[p] = key;
    }
    pad[3] = (pad[3] << 1) | ((pad[0] ^ pad[2]) >> 31);
    for (int p = 4; p < 127; ++p) {
        pad[p] = ((pad[p - 4] ^ pad[p - 2]) << 1) | ((pad[p - 3] ^ pad[p - 1]) >> 31);
    }
    for (int p = 0; p < 127; ++p) {
        pad[p] = swap_be32(pad[p]);
    }
    size_t p = 127;
    for (size_t i = 0; i < len_words; ++i) {
        ++p;
        pad[(p - 1) & 127] = pad[p & 127] ^ pad[(p + 64) & 127];
        data[i] ^= pad[(p - 1) & 127];
    }
}

struct ArwMeta {
    uint32_t raw_width = 0;
    uint32_t raw_height = 0;
    uint32_t bits_per_sample = 12;
    uint32_t compression = 0;
    uint32_t orientation = 1;
    std::vector<uint32_t> strip_offsets;
    std::vector<uint32_t> strip_byte_counts;
    uint32_t rows_per_strip = 0;
    uint16_t sony_curve_raw[4] = {0, 0, 0, 0};
    bool has_sony_curve = false;
    float black_level[4] = {512.0f, 512.0f, 512.0f, 512.0f};
    float white_level = 16380.0f;
    float wb_rggb[4] = {2520.0f, 1024.0f, 1544.0f, 1024.0f};
    bool has_wb = false;
    uint32_t sr2_offset = 0;
    uint32_t sr2_length = 0;
    uint32_t sr2_key = 0;
};

void read_tag_u32_array(const uint8_t* buf, size_t len, bool le, uint16_t type,
                        uint32_t count, uint32_t val_or_off,
                        std::vector<uint32_t>& out) {
    out.clear();
    if (count == 0 || count > 65536) return;
    size_t elem_size = (type == 3 || type == 8) ? 2 : 4;
    if (count * elem_size <= 4) {
        // Inline values packed in little/big endian 4-byte field
        if (count == 1) {
            out.push_back(elem_size == 2 ? (le ? (val_or_off & 0xFFFFu) : (val_or_off >> 16))
                                         : val_or_off);
        } else if (count == 2 && elem_size == 2) {
            out.push_back(le ? (val_or_off & 0xFFFFu) : (val_or_off >> 16));
            out.push_back(le ? (val_or_off >> 16) : (val_or_off & 0xFFFFu));
        }
        return;
    }
    if (static_cast<size_t>(val_or_off) + count * elem_size > len) return;
    out.reserve(count);
    for (uint32_t i = 0; i < count; ++i) {
        size_t pos = static_cast<size_t>(val_or_off) + i * elem_size;
        if (elem_size == 2) {
            out.push_back(read_u16(buf + pos, le));
        } else {
            out.push_back(read_u32(buf + pos, le));
        }
    }
}

void parse_ifd_recursive(const uint8_t* buf, size_t len, bool le, uint32_t ifd_off,
                         std::unordered_set<uint32_t>& visited, ArwMeta& meta, int depth) {
    if (depth > 8 || visited.size() > 32) return;
    if (ifd_off == 0 || static_cast<size_t>(ifd_off) + 2 > len) return;
    if (!visited.insert(ifd_off).second) return;

    uint16_t count = read_u16(buf + ifd_off, le);
    if (count > 512 || static_cast<size_t>(ifd_off) + 2 + count * 12ULL + 4 > len) return;

    uint32_t local_w = 0, local_h = 0, local_bps = 12, local_comp = 0, local_rps = 0;
    std::vector<uint32_t> local_strips, local_counts;
    std::vector<uint32_t> sub_ifds;
    uint32_t exif_ifd = 0;

    for (uint16_t i = 0; i < count; ++i) {
        size_t e = static_cast<size_t>(ifd_off) + 2 + i * 12ULL;
        uint16_t tag = read_u16(buf + e, le);
        uint16_t type = read_u16(buf + e + 2, le);
        uint32_t cnt = read_u32(buf + e + 4, le);
        uint32_t val = read_u32(buf + e + 8, le);
        uint32_t short_val = (type == 3 && cnt == 1 && !le) ? (val >> 16) : (val & 0xFFFFu);
        uint32_t scalar = (type == 3) ? short_val : val;

        switch (tag) {
            case 0x0100: local_w = scalar; break;
            case 0x0101: local_h = scalar; break;
            case 0x0102: local_bps = scalar; break;
            case 0x0103: local_comp = scalar; break;
            case 0x0112:
                if (meta.orientation == 1 && scalar >= 1 && scalar <= 8) meta.orientation = scalar;
                break;
            case 0x0111: read_tag_u32_array(buf, len, le, type, cnt, val, local_strips); break;
            case 0x0116: local_rps = scalar; break;
            case 0x0117: read_tag_u32_array(buf, len, le, type, cnt, val, local_counts); break;
            case 0x014A: read_tag_u32_array(buf, len, le, type, cnt, val, sub_ifds); break;
            case 0x8769: exif_ifd = val; break;
            case 0x7010:
                if (cnt == 4 && static_cast<size_t>(val) + 8 <= len) {
                    for (int k = 0; k < 4; ++k) {
                        meta.sony_curve_raw[k] = read_u16(buf + val + k * 2, le);
                    }
                    meta.has_sony_curve = true;
                }
                break;
            case 0x7200: meta.sr2_offset = val; break;
            case 0x7201: meta.sr2_length = val; break;
            case 0x7221: meta.sr2_key = val; break;
            case 0x7310:
                if (cnt == 4 && static_cast<size_t>(val) + 8 <= len) {
                    for (int k = 0; k < 4; ++k) {
                        meta.black_level[k] = static_cast<float>(read_u16(buf + val + k * 2, le));
                    }
                }
                break;
            case 0x7313:
                if (cnt == 4 && static_cast<size_t>(val) + 8 <= len) {
                    float r = static_cast<float>(read_s16(buf + val + 0, le));
                    float g1 = static_cast<float>(read_s16(buf + val + 2, le));
                    float b = static_cast<float>(read_s16(buf + val + 4, le));
                    float g2 = static_cast<float>(read_s16(buf + val + 6, le));
                    if (r > 10.0f && g1 > 10.0f && b > 10.0f && g2 > 10.0f) {
                        meta.wb_rggb[0] = r;
                        meta.wb_rggb[1] = g1;
                        meta.wb_rggb[2] = b;
                        meta.wb_rggb[3] = g2;
                        meta.has_wb = true;
                    }
                }
                break;
            case 0xC61D:
                if (scalar > 1000) meta.white_level = static_cast<float>(scalar);
                break;
            default: break;
        }
    }

    bool looks_raw = (local_w >= 1600 && local_h >= 1000 && !local_strips.empty() &&
                      (local_comp == 32767 || local_comp == 1));
    if (looks_raw && (local_w * local_h >= meta.raw_width * meta.raw_height)) {
        meta.raw_width = local_w;
        meta.raw_height = local_h;
        meta.bits_per_sample = local_bps;
        meta.compression = local_comp;
        meta.rows_per_strip = local_rps > 0 ? local_rps : local_h;
        meta.strip_offsets = std::move(local_strips);
        meta.strip_byte_counts = std::move(local_counts);
    }

    for (uint32_t sub : sub_ifds) {
        parse_ifd_recursive(buf, len, le, sub, visited, meta, depth + 1);
    }
    if (exif_ifd != 0) {
        parse_ifd_recursive(buf, len, le, exif_ifd, visited, meta, depth + 1);
    }
    uint32_t next_ifd = read_u32(buf + ifd_off + 2 + count * 12ULL, le);
    if (next_ifd != 0) {
        parse_ifd_recursive(buf, len, le, next_ifd, visited, meta, depth + 1);
    }
}

void parse_sr2_subifd_if_needed(const uint8_t* buf, size_t len, bool le, ArwMeta& meta) {
    if (meta.has_wb || meta.sr2_offset == 0 || meta.sr2_length < 16) return;
    if (static_cast<size_t>(meta.sr2_offset) + meta.sr2_length > len) return;

    std::vector<uint8_t> dec(buf + meta.sr2_offset, buf + meta.sr2_offset + meta.sr2_length);
    size_t words = dec.size() / 4;
    sony_decrypt(reinterpret_cast<uint32_t*>(dec.data()), words, meta.sr2_key);

    if (dec.size() < 4) return;
    uint16_t cnt = read_u16(dec.data(), le);
    if (cnt > 256 || 2 + cnt * 12ULL > dec.size()) return;
    for (uint16_t i = 0; i < cnt; ++i) {
        size_t e = 2 + i * 12ULL;
        uint16_t tag = read_u16(dec.data() + e, le);
        uint32_t c = read_u32(dec.data() + e + 4, le);
        uint32_t val_off = read_u32(dec.data() + e + 8, le);
        if (val_off < meta.sr2_offset) continue;
        uint32_t rel = val_off - meta.sr2_offset;
        if (tag == 0x7310 && c == 4 && static_cast<size_t>(rel) + 8 <= dec.size()) {
            for (int k = 0; k < 4; ++k) {
                meta.black_level[k] = static_cast<float>(read_u16(dec.data() + rel + k * 2, le));
            }
        } else if ((tag == 0x7313 || tag == 0x7312) && c == 4 &&
                   static_cast<size_t>(rel) + 8 <= dec.size()) {
            float r = static_cast<float>(read_s16(dec.data() + rel + 0, le));
            float g1 = static_cast<float>(read_s16(dec.data() + rel + 2, le));
            float b = static_cast<float>(read_s16(dec.data() + rel + 4, le));
            float g2 = static_cast<float>(read_s16(dec.data() + rel + 6, le));
            if (r > 10.0f && g1 > 10.0f && b > 10.0f && g2 > 10.0f) {
                meta.wb_rggb[0] = r;
                meta.wb_rggb[1] = g1;
                meta.wb_rggb[2] = b;
                meta.wb_rggb[3] = g2;
                meta.has_wb = true;
            }
        }
    }
}

void build_sony_curve_14bit(const ArwMeta& meta, std::vector<float>& curve_out) {
    curve_out.resize(4096);
    if (!meta.has_sony_curve) {
        for (int i = 0; i < 4096; ++i) {
            curve_out[i] = static_cast<float>(i << 2);
        }
        return;
    }
    uint32_t sc[6] = {
        0,
        static_cast<uint32_t>((meta.sony_curve_raw[0] >> 2) & 0xFFFu),
        static_cast<uint32_t>((meta.sony_curve_raw[1] >> 2) & 0xFFFu),
        static_cast<uint32_t>((meta.sony_curve_raw[2] >> 2) & 0xFFFu),
        static_cast<uint32_t>((meta.sony_curve_raw[3] >> 2) & 0xFFFu),
        4095u
    };
    uint32_t curve[4096] = {0};
    curve[0] = 0;
    for (int i = 0; i < 5; ++i) {
        uint32_t start = sc[i] + 1;
        uint32_t end = std::min<uint32_t>(sc[i + 1], 4095u);
        uint32_t step = 1u << i;
        for (uint32_t j = start; j <= end; ++j) {
            curve[j] = curve[j - 1] + step;
        }
    }
    // Fill tail cleanly if sc[4] < 4095
    for (uint32_t j = sc[5]; j < 4096; ++j) {
        if (j > 0 && curve[j] == 0) curve[j] = curve[j - 1] + 16u;
    }
    // Scale by 0.25f to preserve exact fractional precision across 0..16383
    for (int i = 0; i < 4096; ++i) {
        curve_out[i] = static_cast<float>(curve[i]) * 0.25f;
    }
}

bool invert_3x3(const float m[3][3], float inv[3][3]) {
    double a = m[0][0], b = m[0][1], c = m[0][2];
    double d = m[1][0], e = m[1][1], f = m[1][2];
    double g = m[2][0], h = m[2][1], i = m[2][2];
    double A = e * i - f * h;
    double B = -(d * i - f * g);
    double C = d * h - e * g;
    double D = -(b * i - c * h);
    double E = a * i - c * g;
    double F = -(a * h - b * g);
    double G = b * f - c * e;
    double H = -(a * f - c * d);
    double I = a * e - b * d;
    double det = a * A + b * B + c * C;
    if (std::abs(det) < 1e-12) return false;
    double inv_det = 1.0 / det;
    inv[0][0] = static_cast<float>(A * inv_det);
    inv[0][1] = static_cast<float>(D * inv_det);
    inv[0][2] = static_cast<float>(G * inv_det);
    inv[1][0] = static_cast<float>(B * inv_det);
    inv[1][1] = static_cast<float>(E * inv_det);
    inv[1][2] = static_cast<float>(H * inv_det);
    inv[2][0] = static_cast<float>(C * inv_det);
    inv[2][1] = static_cast<float>(F * inv_det);
    inv[2][2] = static_cast<float>(I * inv_det);
    return true;
}

void build_ilce6400_wb_to_srgb(float out_mat[3][3]) {
    // LibRaw / Adobe XYZ_D65 -> CAM matrix verified on Sony ILCE-6400
    const float xyz_to_cam[3][3] = {
        { 0.7657f, -0.2847f, -0.0607f},
        {-0.4083f,  1.1966f,  0.2389f},
        {-0.0684f,  0.1418f,  0.5844f}
    };
    const float xyz_to_srgb[3][3] = {
        { 3.2404542f, -1.5371385f, -0.4985314f},
        {-0.9692660f,  1.8760108f,  0.0415560f},
        { 0.0556434f, -0.2040259f,  1.0572252f}
    };
    const float d65_xyz[3] = {0.95047f, 1.00000f, 1.08883f};
    float cam_d65[3] = {0, 0, 0};
    for (int r = 0; r < 3; ++r) {
        cam_d65[r] = xyz_to_cam[r][0] * d65_xyz[0] +
                     xyz_to_cam[r][1] * d65_xyz[1] +
                     xyz_to_cam[r][2] * d65_xyz[2];
    }

    float cam_to_xyz[3][3];
    invert_3x3(xyz_to_cam, cam_to_xyz);

    float p[3][3] = {};
    for (int r = 0; r < 3; ++r) {
        for (int c = 0; c < 3; ++c) {
            p[r][c] = xyz_to_srgb[r][0] * cam_to_xyz[0][c] +
                      xyz_to_srgb[r][1] * cam_to_xyz[1][c] +
                      xyz_to_srgb[r][2] * cam_to_xyz[2][c];
        }
    }
    // Scale columns by cam_d65 so white-balanced neutral [1,1,1] maps identically to [1,1,1]
    for (int r = 0; r < 3; ++r) {
        for (int c = 0; c < 3; ++c) {
            out_mat[r][c] = p[r][c] * cam_d65[c];
        }
    }
}

struct LinearRgbImage {
    int width = 0;
    int height = 0;
    std::vector<float> rgb; // interleaved R,G,B
};

inline void rotate_dims_and_copy(const float* src, int sw, int sh, uint32_t orientation,
                                 std::vector<float>& dst, int& dw, int& dh) {
    if (orientation == 6) {
        dw = sh;
        dh = sw;
        dst.resize(static_cast<size_t>(dw) * dh * 3);
        for (int y = 0; y < sh; ++y) {
            for (int x = 0; x < sw; ++x) {
                int nx = sh - 1 - y;
                int ny = x;
                const float* s = src + (static_cast<size_t>(y) * sw + x) * 3;
                float* d = dst.data() + (static_cast<size_t>(ny) * dw + nx) * 3;
                d[0] = s[0]; d[1] = s[1]; d[2] = s[2];
            }
        }
    } else if (orientation == 8) {
        dw = sh;
        dh = sw;
        dst.resize(static_cast<size_t>(dw) * dh * 3);
        for (int y = 0; y < sh; ++y) {
            for (int x = 0; x < sw; ++x) {
                int nx = y;
                int ny = sw - 1 - x;
                const float* s = src + (static_cast<size_t>(y) * sw + x) * 3;
                float* d = dst.data() + (static_cast<size_t>(ny) * dw + nx) * 3;
                d[0] = s[0]; d[1] = s[1]; d[2] = s[2];
            }
        }
    } else if (orientation == 3) {
        dw = sw;
        dh = sh;
        dst.resize(static_cast<size_t>(dw) * dh * 3);
        for (int y = 0; y < sh; ++y) {
            for (int x = 0; x < sw; ++x) {
                int nx = sw - 1 - x;
                int ny = sh - 1 - y;
                const float* s = src + (static_cast<size_t>(y) * sw + x) * 3;
                float* d = dst.data() + (static_cast<size_t>(ny) * dw + nx) * 3;
                d[0] = s[0]; d[1] = s[1]; d[2] = s[2];
            }
        }
    } else {
        dw = sw;
        dh = sh;
        dst.assign(src, src + static_cast<size_t>(sw) * sh * 3);
    }
}

void box_resize_rgb(const float* src, int sw, int sh, std::vector<float>& dst, int dw, int dh) {
    dst.assign(static_cast<size_t>(dw) * dh * 3, 0.0f);
    for (int y = 0; y < dh; ++y) {
        int y0 = (y * sh) / dh;
        int y1 = std::max(y0 + 1, ((y + 1) * sh) / dh);
        y1 = std::min(y1, sh);
        for (int x = 0; x < dw; ++x) {
            int x0 = (x * sw) / dw;
            int x1 = std::max(x0 + 1, ((x + 1) * sw) / dw);
            x1 = std::min(x1, sw);
            float r = 0, g = 0, b = 0;
            int cnt = 0;
            for (int sy = y0; sy < y1; ++sy) {
                const float* row = src + static_cast<size_t>(sy) * sw * 3;
                for (int sx = x0; sx < x1; ++sx) {
                    const float* p = row + sx * 3;
                    r += p[0];
                    g += p[1];
                    b += p[2];
                    ++cnt;
                }
            }
            float inv = cnt > 0 ? (1.0f / static_cast<float>(cnt)) : 1.0f;
            float* out = dst.data() + (static_cast<size_t>(y) * dw + x) * 3;
            out[0] = r * inv;
            out[1] = g * inv;
            out[2] = b * inv;
        }
    }
}

// Reconstruct clipped sensor channels neutrally to eliminate magenta/green casts
// when Highlights/Exposure pull down saturated raw pixels.
inline void reconstruct_clipped_rgb(float& r, float& g, float& b,
                                    float r_norm, float g_norm, float b_norm,
                                    float wb_r, float wb_b) {
    const float clip = 0.965f;
    bool cr = r_norm >= clip;
    bool cg = g_norm >= clip;
    bool cb = b_norm >= clip;
    if (!(cr || cg || cb)) return;
    if (cg && cr && cb) {
        float m = std::max(r, std::max(g, b));
        r = g = b = m;
        return;
    }
    if (cg && !cr && !cb) {
        float lum_est = std::max(g, 0.5f * (r / wb_r + b / wb_b) * 1.0f);
        g = std::max(g, std::min(lum_est, std::max(r, b)));
    }
    // Smooth desaturation above white ceiling when G saturates before R/B channels
    float max_c = std::max(r, std::max(g, b));
    float min_c = std::min(r, std::min(g, b));
    if (cg && max_c > 1.0f) {
        float blend = std::min(1.0f, (g_norm - clip) / (1.0f - clip + 0.02f));
        float target = std::max(g, 0.5f * (max_c + min_c));
        r = r * (1.0f - blend) + target * blend;
        g = g * (1.0f - blend) + target * blend;
        b = b * (1.0f - blend) + target * blend;
    }
}

bool decode_arw_to_linear(const uint8_t* buf, size_t len, int max_dim, bool superpixel_fast,
                          LinearRgbImage& out) {
    if (len < 16) return false;
    bool le = (buf[0] == 'I' && buf[1] == 'I');
    bool be = (buf[0] == 'M' && buf[1] == 'M');
    if (!le && !be) return false;
    if (read_u16(buf + 2, le) != 42) return false;

    uint32_t ifd0 = read_u32(buf + 4, le);
    ArwMeta meta;
    std::unordered_set<uint32_t> visited;
    parse_ifd_recursive(buf, len, le, ifd0, visited, meta, 0);
    parse_sr2_subifd_if_needed(buf, len, le, meta);

    if (meta.raw_width < 1600 || meta.raw_height < 1000 || meta.strip_offsets.empty()) {
        return false;
    }

    uint32_t strip_off = meta.strip_offsets[0];
    if (meta.compression == 32767) {
        size_t need = static_cast<size_t>(meta.raw_width) * meta.raw_height;
        if (meta.strip_offsets.size() == 1 && static_cast<size_t>(strip_off) + need > len) {
            return false;
        }
    }

    std::vector<float> curve14;
    build_sony_curve_14bit(meta, curve14);

    float wb_g_avg = 0.5f * (meta.wb_rggb[1] + meta.wb_rggb[3]);
    float wb_r = meta.wb_rggb[0] / std::max(1.0f, wb_g_avg);
    float wb_g = 1.0f;
    float wb_b = meta.wb_rggb[2] / std::max(1.0f, wb_g_avg);

    float mat[3][3];
    build_ilce6400_wb_to_srgb(mat);

    const float white_span = std::max(1000.0f, meta.white_level - meta.black_level[0]);
    const float inv_span = 1.0f / white_span;

    // Active image crop (ILCE-6400 6048x4024 -> 6000x4000 with 12px border)
    int crop_x = 0, crop_y = 0;
    int active_w = static_cast<int>(meta.raw_width);
    int active_h = static_cast<int>(meta.raw_height);
    if (active_w == 6048 && active_h == 4024) {
        crop_x = 12;
        crop_y = 12;
        active_w = 6000;
        active_h = 4000;
    } else if (active_w == 6024 && active_h == 4024) {
        crop_x = 12;
        crop_y = 12;
        active_w = 6000;
        active_h = 4000;
    }

    // Build full Bayer float buffer (normalized 0..1+ post-black, pre-WB) on active region?
    // Even faster for superpixel_fast: decode compressed rows sequentially into 2 row buffers!
    const int rw = static_cast<int>(meta.raw_width);
    const int rh = static_cast<int>(meta.raw_height);

    auto get_strip_row = [&](int r) -> const uint8_t* {
        if (meta.strip_offsets.size() == 1) {
            size_t off = static_cast<size_t>(strip_off) + static_cast<size_t>(r) * rw;
            return (off + rw <= len) ? (buf + off) : nullptr;
        }
        uint32_t rps = std::max<uint32_t>(1, meta.rows_per_strip);
        size_t sidx = static_cast<size_t>(r) / rps;
        if (sidx >= meta.strip_offsets.size()) return nullptr;
        size_t off = static_cast<size_t>(meta.strip_offsets[sidx]) +
                     (static_cast<size_t>(r) % rps) * rw;
        return (off + rw <= len) ? (buf + off) : nullptr;
    };

    auto decode_arw2_row = [&](int r, uint16_t* row_pix11) -> bool {
        const uint8_t* data = get_strip_row(r);
        if (!data) return false;
        int col = 0;
        const uint8_t* dp = data;
        uint16_t pix[16];
        while (col < rw - 30) {
            uint32_t val = read_u32(dp, true);
            int max_v = static_cast<int>(val & 0x7FFu);
            int min_v = static_cast<int>((val >> 11) & 0x7FFu);
            int imax = static_cast<int>((val >> 22) & 0x0Fu);
            int imin = static_cast<int>((val >> 26) & 0x0Fu);
            int sh = 0;
            while (sh < 4 && (0x80 << sh) <= (max_v - min_v)) {
                ++sh;
            }
            int bit = 30;
            for (int i = 0; i < 16; ++i) {
                if (i == imax) {
                    pix[i] = static_cast<uint16_t>(max_v);
                } else if (i == imin) {
                    pix[i] = static_cast<uint16_t>(min_v);
                } else {
                    int byte_idx = bit >> 3;
                    uint32_t word = static_cast<uint32_t>(dp[byte_idx]) |
                                    (static_cast<uint32_t>(dp[byte_idx + 1]) << 8);
                    int delta = static_cast<int>((word >> (bit & 7)) & 0x7Fu);
                    int pval = (delta << sh) + min_v;
                    if (pval > 0x7FF) pval = 0x7FF;
                    pix[i] = static_cast<uint16_t>(pval);
                    bit += 7;
                }
            }
            for (int i = 0; i < 16; ++i, col += 2) {
                if (col < rw) {
                    row_pix11[col] = pix[i];
                }
            }
            col -= (col & 1) ? 1 : 31;
            dp += 16;
        }
        return true;
    };

    if (meta.compression != 32767) {
        return false; // Currently tuned for ARW2 compressed 14-bit (SonyRawFileType=2)
    }

    std::vector<float> work_rgb;
    int work_w = 0;
    int work_h = 0;

    if (superpixel_fast) {
        // Superpixel 2x2 Bayer -> 3000x2000 direct linear RGB
        work_w = active_w / 2;
        work_h = active_h / 2;
        work_rgb.resize(static_cast<size_t>(work_w) * work_h * 3);
        std::vector<uint16_t> row0(rw), row1(rw);
        const float b0 = meta.black_level[0];
        const float b1 = meta.black_level[1];
        const float b2 = meta.black_level[2];
        const float b3 = meta.black_level[3];

        for (int hy = 0; hy < work_h; ++hy) {
            int sy = crop_y + hy * 2;
            if (!decode_arw2_row(sy, row0.data()) || !decode_arw2_row(sy + 1, row1.data())) {
                return false;
            }
            float* dst = work_rgb.data() + static_cast<size_t>(hy) * work_w * 3;
            for (int hx = 0; hx < work_w; ++hx) {
                int sx = crop_x + hx * 2;
                float r_norm = std::max(0.0f, (curve14[row0[sx] << 1] - b0) * inv_span);
                float g1_norm = std::max(0.0f, (curve14[row0[sx + 1] << 1] - b1) * inv_span);
                float g2_norm = std::max(0.0f, (curve14[row1[sx] << 1] - b3) * inv_span);
                float b_norm = std::max(0.0f, (curve14[row1[sx + 1] << 1] - b2) * inv_span);
                float g_norm = 0.5f * (g1_norm + g2_norm);

                float rwv = r_norm * wb_r;
                float gwv = g_norm * wb_g;
                float bwv = b_norm * wb_b;
                reconstruct_clipped_rgb(rwv, gwv, bwv, r_norm, g_norm, b_norm, wb_r, wb_b);

                float sr = std::max(0.0f, mat[0][0] * rwv + mat[0][1] * gwv + mat[0][2] * bwv);
                float sg = std::max(0.0f, mat[1][0] * rwv + mat[1][1] * gwv + mat[1][2] * bwv);
                float sb = std::max(0.0f, mat[2][0] * rwv + mat[2][1] * gwv + mat[2][2] * bwv);

                dst[hx * 3 + 0] = sr;
                dst[hx * 3 + 1] = sg;
                dst[hx * 3 + 2] = sb;
            }
        }
    } else {
        // Full-res bilinear Bayer demosaic on active crop
        std::vector<float> bayer(static_cast<size_t>(active_w) * active_h);
        std::vector<uint16_t> row_buf(rw);
        for (int y = 0; y < active_h; ++y) {
            if (!decode_arw2_row(crop_y + y, row_buf.data())) return false;
            int row_phase = (y & 1) << 1;
            float* dst_row = bayer.data() + static_cast<size_t>(y) * active_w;
            for (int x = 0; x < active_w; ++x) {
                int cidx = row_phase | (x & 1); // 0=R, 1=G1, 2=G2, 3=B
                float bk = meta.black_level[cidx == 2 ? 3 : (cidx == 3 ? 2 : cidx)];
                dst_row[x] = std::max(0.0f, (curve14[row_buf[crop_x + x] << 1] - bk) * inv_span);
            }
        }

        work_w = active_w;
        work_h = active_h;
        work_rgb.resize(static_cast<size_t>(work_w) * work_h * 3);

        auto at = [&](int y, int x) -> float {
            y = std::clamp(y, 0, active_h - 1);
            x = std::clamp(x, 0, active_w - 1);
            return bayer[static_cast<size_t>(y) * active_w + x];
        };

        for (int y = 0; y < active_h; ++y) {
            int y0 = (y > 0) ? y - 1 : 0;
            int y1 = (y + 1 < active_h) ? y + 1 : active_h - 1;
            const bool even_y = ((y & 1) == 0);
            float* dst = work_rgb.data() + static_cast<size_t>(y) * active_w * 3;
            for (int x = 0; x < active_w; ++x) {
                int x0 = (x > 0) ? x - 1 : 0;
                int x1 = (x + 1 < active_w) ? x + 1 : active_w - 1;
                const bool even_x = ((x & 1) == 0);

                float r_norm, g_norm, b_norm;
                float center = at(y, x);
                if (even_y && even_x) { // R
                    r_norm = center;
                    g_norm = 0.25f * (at(y, x0) + at(y, x1) + at(y0, x) + at(y1, x));
                    b_norm = 0.25f * (at(y0, x0) + at(y0, x1) + at(y1, x0) + at(y1, x1));
                } else if (even_y && !even_x) { // G on R row
                    r_norm = 0.5f * (at(y, x0) + at(y, x1));
                    g_norm = center;
                    b_norm = 0.5f * (at(y0, x) + at(y1, x));
                } else if (!even_y && even_x) { // G on B row
                    r_norm = 0.5f * (at(y0, x) + at(y1, x));
                    g_norm = center;
                    b_norm = 0.5f * (at(y, x0) + at(y, x1));
                } else { // B
                    r_norm = 0.25f * (at(y0, x0) + at(y0, x1) + at(y1, x0) + at(y1, x1));
                    g_norm = 0.25f * (at(y, x0) + at(y, x1) + at(y0, x) + at(y1, x));
                    b_norm = center;
                }

                float rwv = r_norm * wb_r;
                float gwv = g_norm * wb_g;
                float bwv = b_norm * wb_b;
                reconstruct_clipped_rgb(rwv, gwv, bwv, r_norm, g_norm, b_norm, wb_r, wb_b);

                float sr = std::max(0.0f, mat[0][0] * rwv + mat[0][1] * gwv + mat[0][2] * bwv);
                float sg = std::max(0.0f, mat[1][0] * rwv + mat[1][1] * gwv + mat[1][2] * bwv);
                float sb = std::max(0.0f, mat[2][0] * rwv + mat[2][1] * gwv + mat[2][2] * bwv);

                dst[x * 3 + 0] = sr;
                dst[x * 3 + 1] = sg;
                dst[x * 3 + 2] = sb;
            }
        }
    }

    // Resize to max_dim before orientation if needed
    int longer = std::max(work_w, work_h);
    std::vector<float> scaled_rgb;
    int sw = work_w, sh = work_h;
    if (max_dim > 0 && longer > max_dim) {
        float sc = static_cast<float>(max_dim) / static_cast<float>(longer);
        sw = std::max(1, static_cast<int>(work_w * sc + 0.5f));
        sh = std::max(1, static_cast<int>(work_h * sc + 0.5f));
        box_resize_rgb(work_rgb.data(), work_w, work_h, scaled_rgb, sw, sh);
    } else {
        scaled_rgb = std::move(work_rgb);
    }

    rotate_dims_and_copy(scaled_rgb.data(), sw, sh, meta.orientation, out.rgb, out.width, out.height);
    return out.width > 0 && out.height > 0;
}

inline uint8_t encode_log2_u8(float x) {
    float v = std::log2(std::max(x, 0.0f) * 16.0f + 1.0f) * (1.0f / 8.0f);
    int q = static_cast<int>(std::clamp(v, 0.0f, 1.0f) * 255.0f + 0.5f);
    return static_cast<uint8_t>(std::clamp(q, 0, 255));
}

struct MappedSource {
    const uint8_t* ptr = nullptr;
    size_t size = 0;
    int owned_fd = -1;

    ~MappedSource() {
        if (ptr && ptr != MAP_FAILED && size > 0) {
            munmap(const_cast<uint8_t*>(ptr), size);
        }
        if (owned_fd >= 0) {
            close(owned_fd);
        }
    }

    bool open_source(const char* path, int external_fd) {
        int fd = external_fd;
        if (fd < 0 && path && path[0] != '\0') {
            owned_fd = open(path, O_RDONLY);
            if (owned_fd < 0) return false;
            fd = owned_fd;
        }
        if (fd < 0) return false;
        struct stat st{};
        if (fstat(fd, &st) != 0 || st.st_size <= 0) return false;
        size = static_cast<size_t>(st.st_size);
        void* m = mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
        if (m == MAP_FAILED) return false;
        ptr = static_cast<const uint8_t*>(m);
        return true;
    }
};

} // namespace

#ifdef __ANDROID__
extern "C" {

JNIEXPORT jobject JNICALL
Java_com_bionz_looklab_NativeRawDecoder_nativeDecodeArwProxy(
    JNIEnv* env, jclass /*clazz*/, jstring jpath, jint jfd, jint jmax_dim) {
    const char* path = jpath ? env->GetStringUTFChars(jpath, nullptr) : nullptr;
    MappedSource src;
    bool mapped = src.open_source(path, static_cast<int>(jfd));
    if (jpath && path) env->ReleaseStringUTFChars(jpath, path);
    if (!mapped) return nullptr;

    LinearRgbImage lin;
    if (!decode_arw_to_linear(src.ptr, src.size, std::max(360, static_cast<int>(jmax_dim)), true, lin)) {
        return nullptr;
    }

    const size_t px_count = static_cast<size_t>(lin.width) * lin.height;
    std::vector<uint8_t> rgba(px_count * 4);
    for (size_t i = 0; i < px_count; ++i) {
        rgba[i * 4 + 0] = encode_log2_u8(lin.rgb[i * 3 + 0]);
        rgba[i * 4 + 1] = encode_log2_u8(lin.rgb[i * 3 + 1]);
        rgba[i * 4 + 2] = encode_log2_u8(lin.rgb[i * 3 + 2]);
        rgba[i * 4 + 3] = 255;
    }

    int hist_w = (lin.width >= lin.height) ? 160 : 106;
    int hist_h = (lin.width >= lin.height) ? 106 : 160;
    std::vector<float> hist_lin;
    box_resize_rgb(lin.rgb.data(), lin.width, lin.height, hist_lin, hist_w, hist_h);
    std::vector<uint8_t> hist_rgb(static_cast<size_t>(hist_w) * hist_h * 3);
    for (size_t i = 0; i < hist_rgb.size(); ++i) {
        hist_rgb[i] = encode_log2_u8(hist_lin[i]);
    }

    jclass map_cls = env->FindClass("java/util/HashMap");
    jmethodID map_init = env->GetMethodID(map_cls, "<init>", "()V");
    jmethodID map_put = env->GetMethodID(
        map_cls, "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;");
    jclass int_cls = env->FindClass("java/lang/Integer");
    jmethodID int_val_of = env->GetStaticMethodID(int_cls, "valueOf", "(I)Ljava/lang/Integer;");
    jclass bool_cls = env->FindClass("java/lang/Boolean");
    jmethodID bool_val_of = env->GetStaticMethodID(bool_cls, "valueOf", "(Z)Ljava/lang/Boolean;");

    jobject result = env->NewObject(map_cls, map_init);

    auto put_int = [&](const char* k, int v) {
        jstring jk = env->NewStringUTF(k);
        jobject jv = env->CallStaticObjectMethod(int_cls, int_val_of, v);
        env->CallObjectMethod(result, map_put, jk, jv);
        env->DeleteLocalRef(jk);
        env->DeleteLocalRef(jv);
    };
    auto put_bool = [&](const char* k, bool v) {
        jstring jk = env->NewStringUTF(k);
        jobject jv = env->CallStaticObjectMethod(bool_cls, bool_val_of, v ? JNI_TRUE : JNI_FALSE);
        env->CallObjectMethod(result, map_put, jk, jv);
        env->DeleteLocalRef(jk);
        env->DeleteLocalRef(jv);
    };

    jbyteArray jrgba = env->NewByteArray(static_cast<jsize>(rgba.size()));
    env->SetByteArrayRegion(jrgba, 0, static_cast<jsize>(rgba.size()),
                            reinterpret_cast<const jbyte*>(rgba.data()));
    jstring k_rgba = env->NewStringUTF("rgba");
    env->CallObjectMethod(result, map_put, k_rgba, jrgba);
    env->DeleteLocalRef(k_rgba);
    env->DeleteLocalRef(jrgba);

    jbyteArray jhist = env->NewByteArray(static_cast<jsize>(hist_rgb.size()));
    env->SetByteArrayRegion(jhist, 0, static_cast<jsize>(hist_rgb.size()),
                            reinterpret_cast<const jbyte*>(hist_rgb.data()));
    jstring k_hist = env->NewStringUTF("histRgb");
    env->CallObjectMethod(result, map_put, k_hist, jhist);
    env->DeleteLocalRef(k_hist);
    env->DeleteLocalRef(jhist);

    put_int("width", lin.width);
    put_int("height", lin.height);
    put_int("histW", hist_w);
    put_int("histH", hist_h);
    put_bool("isLog2", true);
    put_bool("isTrueRaw", true);

    LOGI("Decoded ARW proxy %dx%d (Log2 14-stop linear)", lin.width, lin.height);
    return result;
}

JNIEXPORT jobject JNICALL
Java_com_bionz_looklab_NativeRawDecoder_nativeDecodeArwFullResLinear(
    JNIEnv* env, jclass /*clazz*/, jstring jpath, jint jfd, jint jmax_dim) {
    const char* path = jpath ? env->GetStringUTFChars(jpath, nullptr) : nullptr;
    MappedSource src;
    bool mapped = src.open_source(path, static_cast<int>(jfd));
    if (jpath && path) env->ReleaseStringUTFChars(jpath, path);
    if (!mapped) return nullptr;

    LinearRgbImage lin;
    if (!decode_arw_to_linear(src.ptr, src.size, static_cast<int>(jmax_dim), false, lin)) {
        return nullptr;
    }

    jclass map_cls = env->FindClass("java/util/HashMap");
    jmethodID map_init = env->GetMethodID(map_cls, "<init>", "()V");
    jmethodID map_put = env->GetMethodID(
        map_cls, "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;");
    jclass int_cls = env->FindClass("java/lang/Integer");
    jmethodID int_val_of = env->GetStaticMethodID(int_cls, "valueOf", "(I)Ljava/lang/Integer;");

    jobject result = env->NewObject(map_cls, map_init);

    jfloatArray jrgb = env->NewFloatArray(static_cast<jsize>(lin.rgb.size()));
    env->SetFloatArrayRegion(jrgb, 0, static_cast<jsize>(lin.rgb.size()), lin.rgb.data());
    jstring k_rgb = env->NewStringUTF("rgb");
    env->CallObjectMethod(result, map_put, k_rgb, jrgb);
    env->DeleteLocalRef(k_rgb);
    env->DeleteLocalRef(jrgb);

    jstring k_w = env->NewStringUTF("width");
    jobject jw = env->CallStaticObjectMethod(int_cls, int_val_of, lin.width);
    env->CallObjectMethod(result, map_put, k_w, jw);
    env->DeleteLocalRef(k_w);
    env->DeleteLocalRef(jw);

    jstring k_h = env->NewStringUTF("height");
    jobject jh = env->CallStaticObjectMethod(int_cls, int_val_of, lin.height);
    env->CallObjectMethod(result, map_put, k_h, jh);
    env->DeleteLocalRef(k_h);
    env->DeleteLocalRef(jh);

    LOGI("Decoded ARW full-res linear %dx%d", lin.width, lin.height);
    return result;
}

} // extern "C"
#endif
