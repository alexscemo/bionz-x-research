/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Contract-driven Synopsys DWC3 (2.90a / 0x5533290A) gadget model for
 * Sony CXD90045 (sony-a6400).
 *
 * MMIO window: 0xF020C000..0xF020FFFF (page-aligned 16 KiB; DWC3 core
 * registers begin at +0x100 = 0xF020C100), GIC IRQ 229.
 *
 * Implements the register, GEVNT DMA ring, DEPCMD, and TRB transfer
 * contracts observed in ILCE-6400 nflasha3-Image (E0579, E0590) with an
 * optional CharBackend virtual USB host socket for EP0/Bulk testing.
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/qdev-properties-system.h"
#include "migration/vmstate.h"
#include "chardev/char-fe.h"
#include "system/dma.h"
#include "system/address-spaces.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD90045_DWC3 "cxd90045-dwc3"
OBJECT_DECLARE_SIMPLE_TYPE(CxdDwc3State, CXD90045_DWC3)

#define DWC3_MMIO_SIZE      0x4000
#define DWC3_CORE_BASE      0x0100
#define DWC3_MAX_EPS        16

/* Global registers (relative to 0xF020C000) */
#define REG_GCTL            0x0110
#define REG_GGPIO           0x0124
#define REG_GUID            0x0128
#define REG_GSNPSID         0x0120
#define REG_GHWPARAMS0      0x0140
#define REG_GHWPARAMS7      0x015C
#define REG_GUSB2PHYCFG0    0x0200
#define REG_GUSB3PIPECTL0   0x02C0
#define REG_GEVNTADRLO0     0x0400
#define REG_GEVNTADRHI0     0x0404
#define REG_GEVNTSIZ0       0x0408
#define REG_GEVNTCOUNT0     0x040C
#define REG_GHWPARAMS8      0x0600

/* Device registers */
#define REG_DCFG            0x0700
#define REG_DCTL            0x0704
#define REG_DEVTEN          0x0708
#define REG_DSTS            0x070C
#define REG_DGCMDPAR        0x0710
#define REG_DGCMD           0x0714
#define REG_DALEPENA        0x0720

/* Physical EP command block: 0x0800 + ep * 0x10 */
#define REG_EP_BASE         0x0800
#define REG_EP_END          0x0900

/* Bit definitions */
#define DCTL_RUN_STOP       (1U << 31)
#define DCTL_CSFTRST        (1U << 30)
#define DSTS_DEVCTRLHLT     (1U << 22)
#define GEVNTSIZ_INTMASK    (1U << 31)

#define DEPCMD_CMDACT       (1U << 10)
#define DEPCMD_CMDIOC       (1U << 8)
#define DEPCMD_CMD_MASK     0x0FU

#define DEPCMD_SETEPCONFIG      1U
#define DEPCMD_SETTRANSFRES     2U
#define DEPCMD_GETSEQNUMBER     3U
#define DEPCMD_SETSTALL         4U
#define DEPCMD_CLEARSTALL       5U
#define DEPCMD_STARTTRANSFER    6U
#define DEPCMD_UPDATETRANSFER   7U
#define DEPCMD_ENDTRANSFER      8U
#define DEPCMD_STARTNEWCONFIG   9U

/* Event types */
#define DEVT_DISCONNECT     0U
#define DEVT_USBRESET       1U
#define DEVT_CONNECTDONE    2U

#define DEPEVT_XFERCOMPLETE 1U
#define DEPEVT_XFERINPROG   2U
#define DEPEVT_XFERNOTREADY 3U
#define DEPEVT_EPCMDCMPLT   7U
#define DEPEVT_STATUS_SHORT (1U << 1)
#define DEPEVT_STATUS_LST   (1U << 3)

#define TRB_CTRL_HWO        (1U << 0)
#define TRB_CTRL_LST        (1U << 1)
#define TRB_CTRL_CHN        (1U << 2)
#define TRB_CTRL_IOC        (1U << 11)

typedef struct CxdDwc3Trb {
    uint32_t bpl;
    uint32_t bph;
    uint32_t size;
    uint32_t ctrl;
} CxdDwc3Trb;

typedef struct CxdDwc3Ep {
    uint32_t par2;
    uint32_t par1;
    uint32_t par0;
    uint32_t cmd;
    uint32_t cfg0;
    uint32_t cfg1;
    uint64_t trb_addr;
    uint32_t out_offset;
    bool trb_active;
    bool stalled;
    uint8_t pending_out[1024];
    uint32_t pending_out_len;
    bool has_pending_out;
    bool nrdy_pending;
} CxdDwc3Ep;

struct CxdDwc3State {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    MemoryRegion captured_start_gate_mr;
    qemu_irq irq;
    CharFrontend chr;

    uint32_t gctl;
    uint32_t ggpio;
    uint32_t guid;
    uint32_t gusb2phycfg0;
    uint32_t gusb3pipectl0;

    uint32_t gevntadrlo;
    uint32_t gevntadrhi;
    uint32_t gevntsiz;
    uint32_t gevntcount;
    uint32_t gevnt_head;

    uint32_t dcfg;
    uint32_t dctl;
    uint32_t devten;
    uint32_t dsts;
    uint32_t dgcmdpar;
    uint32_t dgcmd;
    uint32_t dalepena;

    bool host_connected;
    bool captured_hwparams;
    bool captured_start_gate;
    bool strict_ep0;
    bool strict_bulk;
    bool allow_setup_abort;
    uint8_t control_stage; /* 0 SETUP, 1 DATA, 2 STATUS; host protocol bookkeeping */
    bool control_data_in;
    bool control_has_data;
    CxdDwc3Ep eps[DWC3_MAX_EPS];

    char rx_line[2200];
    size_t rx_len;
    bool rx_overflow;
};

static void dwc3_update_irq(CxdDwc3State *s)
{
    bool masked = (s->gevntsiz & GEVNTSIZ_INTMASK) != 0;
    int level = (!masked && s->gevntcount > 0) ? 1 : 0;
    qemu_set_irq(s->irq, level);
}

static void dwc3_push_event(CxdDwc3State *s, uint32_t ev)
{
    uint32_t ring_bytes = s->gevntsiz & 0xFFFCU;
    uint64_t base = ((uint64_t)s->gevntadrhi << 32) | s->gevntadrlo;
    uint32_t le_ev = cpu_to_le32(ev);

    if (ring_bytes < 4 || base == 0) {
        return;
    }
    if (s->gevntcount + 4 > ring_bytes) {
        return;
    }

    dma_memory_write(&address_space_memory, base + s->gevnt_head,
                     &le_ev, sizeof(le_ev), MEMTXATTRS_UNSPECIFIED);
    s->gevnt_head = (s->gevnt_head + 4) % ring_bytes;
    s->gevntcount += 4;
    dwc3_update_irq(s);
}

static void dwc3_push_devt(CxdDwc3State *s, uint32_t type, uint32_t info)
{
    if (!(s->devten & (1U << type))) {
        return;
    }
    uint32_t ev = 1U | ((type & 0xFU) << 8) | ((info & 0xFFFFU) << 16);
    dwc3_push_event(s, ev);
}

static void dwc3_push_depevt(CxdDwc3State *s, uint32_t ep, uint32_t type,
                             uint32_t status, uint32_t param)
{
    if (s->strict_ep0 && ep < 2 &&
        ((type == DEPEVT_XFERCOMPLETE && !(s->eps[ep].cfg1 & (1U << 8))) ||
         (type == DEPEVT_XFERNOTREADY && !(s->eps[ep].cfg1 & (1U << 10))))) {
        return;
    }
    uint32_t ev = ((ep & 0x1FU) << 1) | ((type & 0xFU) << 6) |
                  ((status & 0xFU) << 12) | ((param & 0xFFFFU) << 16);
    dwc3_push_event(s, ev);
}

static void dwc3_emit_host_in(CxdDwc3State *s, uint32_t ep,
                              const uint8_t *buf, uint32_t len)
{
    if (!qemu_chr_fe_backend_connected(&s->chr)) {
        return;
    }
    GString *msg = g_string_new(NULL);
    g_string_append_printf(msg, "IN %u ", ep);
    for (uint32_t i = 0; i < len; i++) {
        g_string_append_printf(msg, "%02x", buf[i]);
    }
    g_string_append_c(msg, '\n');
    qemu_chr_fe_write_all(&s->chr, (const uint8_t *)msg->str, msg->len);
    g_string_free(msg, true);
}

static bool dwc3_ep_is_in(uint32_t ep)
{
    return (ep & 1U) != 0;
}

static void dwc3_ep_process_trb(CxdDwc3State *s, uint32_t ep_idx)
{
    CxdDwc3Ep *ep = &s->eps[ep_idx];
    CxdDwc3Trb trb;

    if (s->strict_ep0) {
        return; /* Ownership changes only in response to a host token. */
    }

    if (!ep->trb_active || ep->trb_addr == 0) {
        return;
    }

    dma_memory_read(&address_space_memory, ep->trb_addr,
                    &trb, sizeof(trb), MEMTXATTRS_UNSPECIFIED);
    trb.bpl = le32_to_cpu(trb.bpl);
    trb.bph = le32_to_cpu(trb.bph);
    trb.size = le32_to_cpu(trb.size);
    trb.ctrl = le32_to_cpu(trb.ctrl);

    if (!(trb.ctrl & TRB_CTRL_HWO)) {
        return;
    }

    uint64_t buf_addr = ((uint64_t)trb.bph << 32) | trb.bpl;
    uint32_t buf_len = trb.size & 0x00FFFFFFU;

    if (dwc3_ep_is_in(ep_idx)) {
        uint8_t tmp[1024];
        uint32_t copy_len = MIN(buf_len, (uint32_t)sizeof(tmp));
        if (copy_len > 0 && buf_addr != 0) {
            dma_memory_read(&address_space_memory, buf_addr,
                            tmp, copy_len, MEMTXATTRS_UNSPECIFIED);
            dwc3_emit_host_in(s, ep_idx, tmp, copy_len);
        }
        trb.size = 0;
        trb.ctrl &= ~TRB_CTRL_HWO;
        CxdDwc3Trb out_trb = {
            .bpl = cpu_to_le32(trb.bpl),
            .bph = cpu_to_le32(trb.bph),
            .size = cpu_to_le32(trb.size),
            .ctrl = cpu_to_le32(trb.ctrl),
        };
        dma_memory_write(&address_space_memory, ep->trb_addr,
                         &out_trb, sizeof(out_trb), MEMTXATTRS_UNSPECIFIED);
        ep->trb_active = false;
        dwc3_push_depevt(s, ep_idx, DEPEVT_XFERCOMPLETE,
                     (trb.ctrl & TRB_CTRL_LST) ? 8U : 0U, 0);
        return;
    }

    /* OUT endpoint (including EP0 OUT / SETUP) */
    if (!ep->has_pending_out) {
        return;
    }

    uint32_t xfer = MIN(buf_len, ep->pending_out_len);
    if (xfer > 0 && buf_addr != 0) {
        dma_memory_write(&address_space_memory, buf_addr,
                         ep->pending_out, xfer, MEMTXATTRS_UNSPECIFIED);
    }
    trb.size = (buf_len - xfer) & 0x00FFFFFFU;
    trb.ctrl &= ~TRB_CTRL_HWO;
    CxdDwc3Trb out_trb = {
        .bpl = cpu_to_le32(trb.bpl),
        .bph = cpu_to_le32(trb.bph),
        .size = cpu_to_le32(trb.size),
        .ctrl = cpu_to_le32(trb.ctrl),
    };
    dma_memory_write(&address_space_memory, ep->trb_addr,
                     &out_trb, sizeof(out_trb), MEMTXATTRS_UNSPECIFIED);
    ep->has_pending_out = false;
    ep->pending_out_len = 0;
    ep->trb_active = false;
    dwc3_push_depevt(s, ep_idx, DEPEVT_XFERCOMPLETE,
                     (trb.ctrl & TRB_CTRL_LST) ? 8U : 0U, 0);
}

static void dwc3_device_reset(CxdDwc3State *s)
{
    s->dcfg = 0;
    s->dctl = 0;
    s->devten = 0;
    s->dsts = DSTS_DEVCTRLHLT;
    s->dalepena = 0;
    s->gevntcount = 0;
    s->gevnt_head = 0;
    memset(s->eps, 0, sizeof(s->eps));
    s->control_stage = 0;
    dwc3_update_irq(s);
}

static void dwc3_handle_ep_cmd(CxdDwc3State *s, uint32_t ep_idx, uint32_t val)
{
    CxdDwc3Ep *ep = &s->eps[ep_idx];
    uint32_t cmd = val & DEPCMD_CMD_MASK;

    if (!(val & DEPCMD_CMDACT)) {
        ep->cmd = val;
        return;
    }

    switch (cmd) {
    case DEPCMD_SETEPCONFIG:
        ep->cfg0 = ep->par0;
        ep->cfg1 = ep->par1;
        break;
    case DEPCMD_SETTRANSFRES:
        break;
    case DEPCMD_SETSTALL:
        ep->stalled = true;
        break;
    case DEPCMD_CLEARSTALL:
        ep->stalled = false;
        ep->out_offset = 0;
        ep->nrdy_pending = false;
        break;
    case DEPCMD_STARTTRANSFER:
        ep->trb_addr = ((uint64_t)ep->par0 << 32) | ep->par1;
        ep->trb_active = true;
        ep->out_offset = 0;
        ep->nrdy_pending = false;
        val = (val & ~0x007F0000U) | ((ep_idx + 1U) << 16);
        ep->cmd = val & ~DEPCMD_CMDACT;
        dwc3_ep_process_trb(s, ep_idx);
        return;
    case DEPCMD_UPDATETRANSFER:
        ep->nrdy_pending = false;
        ep->cmd = val & ~DEPCMD_CMDACT;
        dwc3_ep_process_trb(s, ep_idx);
        return;
    case DEPCMD_ENDTRANSFER:
        ep->trb_active = false;
        ep->out_offset = 0;
        ep->nrdy_pending = false;
        if (val & DEPCMD_CMDIOC) {
            dwc3_push_depevt(s, ep_idx, DEPEVT_EPCMDCMPLT, 0, cmd << 8);
        }
        break;
    case DEPCMD_STARTNEWCONFIG:
        for (uint32_t i = 2; i < DWC3_MAX_EPS; i++) {
            s->eps[i].out_offset = 0;
            s->eps[i].nrdy_pending = false;
        }
        break;
    default:
        break;
    }

    ep->cmd = val & ~DEPCMD_CMDACT;
}

static uint64_t cxd_dwc3_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdDwc3State *s = CXD90045_DWC3(opaque);

    /* E0595: physical ILCE-6400 MSC observation, not inferred capabilities.
     * Keep the historical profile available for earlier replay baselines. */
    if (s->captured_hwparams) {
        static const uint32_t params[] = {
            0x4020404aU, 0x0160c93bU, 0x00000001U, 0x06a4d085U,
            0x47822020U, 0x04204108U, 0x0a0a8020U, 0x04881472U,
        };
        if (offset >= REG_GHWPARAMS0 && offset <= REG_GHWPARAMS7 &&
            (offset & 3) == 0) {
            return params[(offset - REG_GHWPARAMS0) / 4];
        }
        if (offset == REG_GHWPARAMS8) {
            return 0x00000a0aU;
        }
    }

    if (offset >= REG_EP_BASE && offset < REG_EP_END) {
        uint32_t ep = (offset - REG_EP_BASE) / 0x10U;
        uint32_t reg = (offset - REG_EP_BASE) % 0x10U;
        switch (reg) {
        case 0x0:
            return s->eps[ep].par2;
        case 0x4:
            return s->eps[ep].par1;
        case 0x8:
            return s->eps[ep].par0;
        case 0xC:
            return s->eps[ep].cmd;
        }
    }

    switch (offset) {
    case REG_GCTL:
        return s->gctl;
    case REG_GSNPSID:
        return 0x5533290AU;
    case REG_GGPIO:
        return s->ggpio;
    case REG_GUID:
        return s->guid;
    case REG_GHWPARAMS0:
        return 0x20204008U;
    case REG_GHWPARAMS0 + 4:
        return 0x00241008U;
    case REG_GHWPARAMS0 + 8:
        return 0x00000000U;
    case REG_GHWPARAMS0 + 0xC:
        return 0x10420089U;
    case REG_GHWPARAMS0 + 0x10:
        return 0x00000000U;
    case REG_GHWPARAMS0 + 0x14:
        return 0x04020200U;
    case REG_GHWPARAMS0 + 0x18:
        return 0x00000400U;
    case REG_GHWPARAMS7:
        return 0x00000000U;
    case REG_GHWPARAMS8:
        return 0x00000000U;
    case REG_GUSB2PHYCFG0:
        return s->gusb2phycfg0;
    case REG_GUSB3PIPECTL0:
        return s->gusb3pipectl0;
    case REG_GEVNTADRLO0:
        return s->gevntadrlo;
    case REG_GEVNTADRHI0:
        return s->gevntadrhi;
    case REG_GEVNTSIZ0:
        return s->gevntsiz;
    case REG_GEVNTCOUNT0:
        return s->gevntcount;
    case REG_DCFG:
        return s->dcfg;
    case REG_DCTL:
        return s->dctl;
    case REG_DEVTEN:
        return s->devten;
    case REG_DSTS:
        return s->dsts;
    case REG_DGCMDPAR:
        return s->dgcmdpar;
    case REG_DGCMD:
        return s->dgcmd;
    case REG_DALEPENA:
        return s->dalepena;
    default:
        qemu_log_mask(LOG_UNIMP, "cxd90045-dwc3: unimp read +0x%04" HWADDR_PRIx "\n",
                      offset);
        return 0;
    }
}

static void cxd_dwc3_write(void *opaque, hwaddr offset, uint64_t val64,
                           unsigned size)
{
    CxdDwc3State *s = CXD90045_DWC3(opaque);
    uint32_t val = (uint32_t)val64;

    if (offset >= REG_EP_BASE && offset < REG_EP_END) {
        uint32_t ep = (offset - REG_EP_BASE) / 0x10U;
        uint32_t reg = (offset - REG_EP_BASE) % 0x10U;
        switch (reg) {
        case 0x0:
            s->eps[ep].par2 = val;
            return;
        case 0x4:
            s->eps[ep].par1 = val;
            return;
        case 0x8:
            s->eps[ep].par0 = val;
            return;
        case 0xC:
            dwc3_handle_ep_cmd(s, ep, val);
            return;
        }
    }

    switch (offset) {
    case REG_GCTL:
        s->gctl = val;
        return;
    case REG_GGPIO:
        s->ggpio = val;
        return;
    case REG_GUID:
        s->guid = val;
        return;
    case REG_GUSB2PHYCFG0:
        s->gusb2phycfg0 = val;
        return;
    case REG_GUSB3PIPECTL0:
        s->gusb3pipectl0 = val;
        return;
    case REG_GEVNTADRLO0:
        s->gevntadrlo = val;
        return;
    case REG_GEVNTADRHI0:
        s->gevntadrhi = val;
        return;
    case REG_GEVNTSIZ0:
        s->gevntsiz = val;
        dwc3_update_irq(s);
        return;
    case REG_GEVNTCOUNT0: {
        uint32_t ack = val & 0xFFFFU;
        s->gevntcount = (ack >= s->gevntcount) ? 0 : (s->gevntcount - ack);
        dwc3_update_irq(s);
        return;
    }
    case REG_DCFG:
        s->dcfg = val;
        return;
    case REG_DCTL:
        if (val & DCTL_CSFTRST) {
            dwc3_device_reset(s);
            return;
        }
        {
            bool was_run = (s->dctl & DCTL_RUN_STOP) != 0;
            bool now_run = (val & DCTL_RUN_STOP) != 0;
            s->dctl = val;
            if (now_run) {
                s->dsts &= ~DSTS_DEVCTRLHLT;
                if (!was_run && s->host_connected) {
                    dwc3_push_devt(s, DEVT_USBRESET, 0);
                    dwc3_push_devt(s, DEVT_CONNECTDONE, 0);
                }
            } else {
                s->dsts |= DSTS_DEVCTRLHLT;
            }
        }
        return;
    case REG_DEVTEN:
        s->devten = val;
        return;
    case REG_DGCMDPAR:
        s->dgcmdpar = val;
        return;
    case REG_DGCMD:
        s->dgcmd = val & ~DEPCMD_CMDACT;
        return;
    case REG_DALEPENA:
        s->dalepena = val;
        return;
    default:
        qemu_log_mask(LOG_UNIMP, "cxd90045-dwc3: unimp write +0x%04" HWADDR_PRIx
                      " = 0x%08x\n", offset, val);
        return;
    }
}

static const MemoryRegionOps cxd_dwc3_ops = {
    .read = cxd_dwc3_read,
    .write = cxd_dwc3_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static size_t decode_hex(const char *hex, uint8_t *out, size_t max_out)
{
    size_t n = 0;
    while (hex[0] && hex[1] && n < max_out) {
        int hi = hex_nibble(hex[0]);
        int lo = hex_nibble(hex[1]);
        if (hi < 0 || lo < 0) {
            break;
        }
        out[n++] = (uint8_t)((hi << 4) | lo);
        hex += 2;
    }
    return n;
}

static void strict_reply(CxdDwc3State *s, const char *text)
{
    qemu_chr_fe_write_all(&s->chr, (const uint8_t *)text, strlen(text));
}

static bool strict_hex(const char *hex, uint8_t *data, uint32_t *length)
{
    size_t n = strlen(hex);
    if (n > 2048 || (n & 1)) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (hex_nibble(hex[i]) < 0) {
            return false;
        }
    }
    *length = decode_hex(hex, data, 1024);
    return true;
}

static bool strict_ram_range(uint64_t address, uint32_t length)
{
    /* Declared sony-a6400 RAM windows, never a catch-all MMIO zero reply. */
    return (address < 0x40000000ULL && length <= 0x40000000ULL - address) ||
           (address >= 0x80000000ULL && address < 0xbf800000ULL &&
            length <= 0xbf800000ULL - address);
}

/* Opt-in bounded EP0 host-token contract. No token is queued across NAK:
 * the host retries after the original driver arms its own TRB. Unsupported
 * lengths, chains and DMA accesses leave ownership unchanged and report an
 * explicit boundary rather than a fabricated successful transfer. */
static void strict_token(CxdDwc3State *s, uint32_t ep_idx, bool setup,
                         const uint8_t *payload, uint32_t length)
{
    CxdDwc3Ep *ep = &s->eps[ep_idx];
    CxdDwc3Trb trb;
    char reply[80];
    uint32_t kind, capacity, expected;
    uint8_t data[1024];
    uint64_t address;
    bool in = dwc3_ep_is_in(ep_idx);

    if (!s->host_connected || !(s->dctl & DCTL_RUN_STOP) ||
        !(s->dalepena & (1U << ep_idx))) {
        snprintf(reply, sizeof(reply), "NAK %u\n", ep_idx);
        strict_reply(s, reply);
        return;
    }
    if (setup) {
        s->eps[0].stalled = false;
        s->eps[1].stalled = false;
    }
    if (ep->stalled || s->eps[0].stalled) {
        s->control_stage = 0;
        snprintf(reply, sizeof(reply), "STALL %u\n", ep_idx);
        strict_reply(s, reply);
        return;
    }
    if (setup && s->control_stage != 0) {
        if (!s->allow_setup_abort) {
            strict_reply(s, "ERROR unsupported-control-abort\n");
            return;
        }
        s->control_stage = 0;
        s->eps[0].nrdy_pending = false;
        s->eps[1].trb_active = false;
        s->eps[1].nrdy_pending = false;
    }
    expected = setup ? 2 : (s->control_stage == 1 ? 5 :
                           (s->control_stage == 2 ? (s->control_has_data ? 4 : 3) : 0));
    if (!setup && (!expected ||
        (in != (s->control_stage == 1 ? s->control_data_in : !s->control_data_in)))) {
        strict_reply(s, "ERROR control-direction-or-stage\n");
        return;
    }
    uint64_t ring_base = ((uint64_t)s->gevntadrhi << 32) | s->gevntadrlo;
    if ((s->gevntsiz & 0xFFFCU) < 4 ||
        !strict_ram_range(ring_base, s->gevntsiz & 0xFFFCU) ||
        s->gevntcount + 4 > (s->gevntsiz & 0xFFFCU)) {
        strict_reply(s, "ERROR event-ring-unavailable\n");
        return;
    }
    if (!ep->trb_active || !ep->trb_addr) {
        goto nak;
    }
    if (!strict_ram_range(ep->trb_addr, sizeof(trb)) ||
        dma_memory_read(&address_space_memory, ep->trb_addr, &trb,
                        sizeof(trb), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
        strict_reply(s, "ERROR trb-dma-read\n");
        return;
    }
    trb.bpl = le32_to_cpu(trb.bpl);
    trb.bph = le32_to_cpu(trb.bph);
    trb.size = le32_to_cpu(trb.size);
    trb.ctrl = le32_to_cpu(trb.ctrl);
    kind = (trb.ctrl >> 4) & 0x3f;
    if (!(trb.ctrl & TRB_CTRL_HWO)) {
        goto nak;
    }
    if (kind != expected) {
        strict_reply(s, "ERROR trb-control-type\n");
        return;
    }
    capacity = trb.size & 0xffffff;
    address = ((uint64_t)trb.bph << 32) | trb.bpl;
    if (capacity > sizeof(data) || (trb.size & 0xff000000U) || (trb.ctrl & 4U)) {
        strict_reply(s, "ERROR unsupported-trb-length-flags-or-chain\n");
        return;
    }
    if ((setup && (length != 8 || capacity < 8)) ||
        (!in && length > capacity) || (s->control_stage == 2 && (length || (in && capacity)))) {
        strict_reply(s, "ERROR token-length\n");
        return;
    }
    if (in && capacity && (!strict_ram_range(address, capacity) ||
        dma_memory_read(&address_space_memory, address, data,
                    capacity, MEMTXATTRS_UNSPECIFIED) != MEMTX_OK)) {
        strict_reply(s, "ERROR payload-dma-read\n");
        return;
    }
    if (!in && length && (!strict_ram_range(address, length) ||
        dma_memory_write(&address_space_memory, address, payload,
                    length, MEMTXATTRS_UNSPECIFIED) != MEMTX_OK)) {
        strict_reply(s, "ERROR payload-dma-write\n");
        return;
    }
    CxdDwc3Trb completed = {
        .bpl = cpu_to_le32(trb.bpl), .bph = cpu_to_le32(trb.bph),
        .size = cpu_to_le32(in ? 0 : capacity - length),
        .ctrl = cpu_to_le32(trb.ctrl & ~TRB_CTRL_HWO),
    };
    if (dma_memory_write(&address_space_memory, ep->trb_addr, &completed,
                        sizeof(completed), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
        strict_reply(s, "ERROR trb-dma-write\n");
        return;
    }
    ep->trb_active = false;
    ep->nrdy_pending = false;
    if (setup) {
        uint32_t requested = payload[6] | (payload[7] << 8);
        s->control_data_in = (payload[0] & 0x80) != 0;
        s->control_has_data = requested != 0;
        s->control_stage = requested ? 1 : 2;
        /* A no-data request always has an IN status phase. */
        if (!requested) {
            s->control_data_in = false;
        }
    } else {
        s->control_stage = s->control_stage == 1 ? 2 : 0;
    }
    dwc3_push_depevt(s, ep_idx, DEPEVT_XFERCOMPLETE,
                     (trb.ctrl & TRB_CTRL_LST) ? 8U : 0U, 0);
    if (in) {
        dwc3_emit_host_in(s, ep_idx, data, capacity);
    } else {
        snprintf(reply, sizeof(reply), "ACK %u %u\n", ep_idx, length);
        strict_reply(s, reply);
    }
    return;
nak:
    if (!setup && !ep->nrdy_pending && (ep->cfg1 & (1U << 10))) {
        dwc3_push_depevt(s, ep_idx, DEPEVT_XFERNOTREADY,
                        s->control_stage == 2 ? 2 : 1, 0);
        ep->nrdy_pending = true;
    }
    snprintf(reply, sizeof(reply), "NAK %u\n", ep_idx);
    strict_reply(s, reply);
}

#define STRICT_BULK_MAX_BYTES (256U * 1024U)
#define STRICT_BULK_MAX_CHAIN_TRBS 64U
#define TRBCTL_NORMAL         1U
#define TRBCTL_LINK           8U

typedef struct StrictBulkOutSegment {
    CxdDwc3Trb trb;
    uint64_t trb_pa;
    uint64_t buf_pa;
    uint32_t capacity;
    uint32_t offset;
    uint32_t bytes;
    uint32_t ctrl;
} StrictBulkOutSegment;

static CxdDwc3Ep *strict_resolve_bulk_in_ep(CxdDwc3State *s, uint32_t ep_idx,
                                            bool *ambiguous)
{
    CxdDwc3Ep *match = NULL;

    *ambiguous = false;
    for (uint32_t i = 2; i < DWC3_MAX_EPS; i++) {
        if ((s->dalepena & (1U << i)) &&
            ((s->eps[i].cfg1 >> 25) & 0x1FU) == ep_idx) {
            if (match) {
                *ambiguous = true;
                return NULL;
            }
            match = &s->eps[i];
        }
    }
    if (match) {
        return match;
    }
    if (ep_idx < DWC3_MAX_EPS && (s->dalepena & (1U << ep_idx))) {
        for (uint32_t i = 2; i < DWC3_MAX_EPS; i++) {
            if (((s->eps[i].cfg1 >> 25) & 0x1FU) == ep_idx) {
                if (match) {
                    *ambiguous = true;
                    return NULL;
                }
                match = &s->eps[i];
            }
        }
        if (match) {
            return match;
        }
        if (((s->eps[ep_idx].cfg1 >> 25) & 0x1FU) == 0) {
            return &s->eps[ep_idx];
        }
    }
    return NULL;
}

static void strict_bulk_in(CxdDwc3State *s, uint32_t ep_idx)
{
    bool ambiguous = false;
    CxdDwc3Ep *ep = strict_resolve_bulk_in_ep(s, ep_idx, &ambiguous);
    CxdDwc3Trb trb;
    char reply[64];
    uint32_t kind, capacity;
    uint64_t address;

    if (!s->host_connected || !(s->dctl & DCTL_RUN_STOP)) {
        snprintf(reply, sizeof(reply), "NAK %u\n", ep_idx);
        strict_reply(s, reply);
        return;
    }
    if (ambiguous) {
        strict_reply(s, "ERROR ambiguous-bulk-in-endpoint\n");
        return;
    }
    if (!ep) {
        snprintf(reply, sizeof(reply), "NAK %u\n", ep_idx);
        strict_reply(s, reply);
        return;
    }
    uint32_t phys_ep = (uint32_t)(ep - s->eps);
    if (ep->stalled) {
        snprintf(reply, sizeof(reply), "STALL %u\n", ep_idx);
        strict_reply(s, reply);
        return;
    }
    uint64_t ring_base = ((uint64_t)s->gevntadrhi << 32) | s->gevntadrlo;
    if ((s->gevntsiz & 0xFFFCU) < 4 ||
        !strict_ram_range(ring_base, s->gevntsiz & 0xFFFCU) ||
        s->gevntcount + 4 > (s->gevntsiz & 0xFFFCU)) {
        strict_reply(s, "ERROR event-ring-unavailable\n");
        return;
    }
    if (!ep->trb_active || !ep->trb_addr) {
        goto nak;
    }
    for (int hop = 0; hop < 2; hop++) {
        if (!strict_ram_range(ep->trb_addr, sizeof(trb)) ||
            dma_memory_read(&address_space_memory, ep->trb_addr, &trb,
                            sizeof(trb), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
            strict_reply(s, "ERROR trb-dma-read\n");
            return;
        }
        trb.bpl = le32_to_cpu(trb.bpl);
        trb.bph = le32_to_cpu(trb.bph);
        trb.size = le32_to_cpu(trb.size);
        trb.ctrl = le32_to_cpu(trb.ctrl);
        if (!(trb.ctrl & TRB_CTRL_HWO)) {
            goto nak;
        }
        kind = (trb.ctrl >> 4) & 0x3f;
        if (kind == TRBCTL_LINK && hop == 0) {
            ep->trb_addr = ((uint64_t)trb.bph << 32) | trb.bpl;
            continue;
        }
        break;
    }
    if (kind != TRBCTL_NORMAL) {
        strict_reply(s, "ERROR trb-bulk-type\n");
        return;
    }
    capacity = trb.size & 0xffffffU;
    address = ((uint64_t)trb.bph << 32) | trb.bpl;
    if (capacity > STRICT_BULK_MAX_BYTES || (trb.size & 0xff000000U)) {
        strict_reply(s, "ERROR unsupported-bulk-length\n");
        return;
    }
    uint8_t *data = capacity ? g_malloc(capacity) : NULL;
    if (capacity && (!strict_ram_range(address, capacity) ||
        dma_memory_read(&address_space_memory, address, data,
                        capacity, MEMTXATTRS_UNSPECIFIED) != MEMTX_OK)) {
        g_free(data);
        strict_reply(s, "ERROR payload-dma-read\n");
        return;
    }
    CxdDwc3Trb completed = {
        .bpl = cpu_to_le32(trb.bpl),
        .bph = cpu_to_le32(trb.bph),
        .size = cpu_to_le32(0),
        .ctrl = cpu_to_le32(trb.ctrl & ~TRB_CTRL_HWO),
    };
    if (dma_memory_write(&address_space_memory, ep->trb_addr, &completed,
                         sizeof(completed), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
        g_free(data);
        strict_reply(s, "ERROR trb-dma-write\n");
        return;
    }
    ep->trb_addr += sizeof(CxdDwc3Trb);
    if (trb.ctrl & TRB_CTRL_LST) {
        ep->trb_active = false;
    }
    ep->nrdy_pending = false;
    dwc3_push_depevt(s, phys_ep, DEPEVT_XFERCOMPLETE,
                     (trb.ctrl & TRB_CTRL_LST) ? 8U : 0U, 0);
    dwc3_emit_host_in(s, ep_idx, data, capacity);
    g_free(data);
    return;
nak:
    if (!ep->nrdy_pending &&
        ((ep->cfg1 & (1U << 10)) || (s->dalepena & (1U << phys_ep)))) {
        dwc3_push_depevt(s, phys_ep, DEPEVT_XFERNOTREADY, 0, 0);
        ep->nrdy_pending = true;
    }
    snprintf(reply, sizeof(reply), "NAK %u\n", ep_idx);
    strict_reply(s, reply);
}

static void strict_bulk_out_chained(CxdDwc3State *s, CxdDwc3Ep *ep,
                                   uint32_t logical, uint32_t phys,
                                   const uint8_t *payload, uint32_t length,
                                   uint32_t max_packet, uint32_t ring_bytes,
                                   uint64_t ring)
{
    StrictBulkOutSegment segments[STRICT_BULK_MAX_CHAIN_TRBS];
    uint64_t visited[STRICT_BULK_MAX_CHAIN_TRBS];
    uint32_t descriptor_count = 0, segment_count = 0;
    uint32_t total_capacity = 0, remaining = length, event_count = 0;
    uint64_t trb_pa = ep->trb_addr;
    char reply[64];

    while (segment_count == 0 || remaining != 0) {
        CxdDwc3Trb trb;
        uint32_t ctrl, kind, capacity, offset, available, bytes;
        bool chain, last;

        if (descriptor_count >= STRICT_BULK_MAX_CHAIN_TRBS ||
            (trb_pa & 15U) || !strict_ram_range(trb_pa, sizeof(trb))) {
            strict_reply(s, "ERROR bulk-out-chain-bounds\n");
            return;
        }
        for (uint32_t i = 0; i < descriptor_count; i++) {
            if (visited[i] == trb_pa) {
                strict_reply(s, "ERROR bulk-out-chain-loop\n");
                return;
            }
        }
        visited[descriptor_count++] = trb_pa;
        if ((trb_pa < ring + ring_bytes && ring < trb_pa + sizeof(trb)) ||
            !address_space_access_valid(&address_space_memory, trb_pa,
                                        sizeof(trb), true,
                                        MEMTXATTRS_UNSPECIFIED) ||
            dma_memory_read(&address_space_memory, trb_pa, &trb,
                            sizeof(trb), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
            strict_reply(s, "ERROR trb-dma-read\n");
            return;
        }
        trb.bpl = le32_to_cpu(trb.bpl);
        trb.bph = le32_to_cpu(trb.bph);
        trb.size = le32_to_cpu(trb.size);
        trb.ctrl = le32_to_cpu(trb.ctrl);
        ctrl = trb.ctrl;
        if (!(ctrl & TRB_CTRL_HWO)) {
            if (segment_count == 0) {
                goto notify_nak;
            }
            strict_reply(s, "ERROR bulk-out-chain-incomplete\n");
            return;
        }
        kind = (ctrl >> 4) & 0x3fU;
        if (kind == TRBCTL_LINK) {
            if (trb.size != 0 || (ctrl & TRB_CTRL_LST)) {
                strict_reply(s, "ERROR unsupported-bulk-out-link\n");
                return;
            }
            trb_pa = ((uint64_t)trb.bph << 32) | trb.bpl;
            continue;
        }
        if (kind != TRBCTL_NORMAL || (trb.size & 0xff000000U) ||
            (ctrl & (TRB_CTRL_LST | TRB_CTRL_CHN)) ==
                (TRB_CTRL_LST | TRB_CTRL_CHN) ||
            (ctrl & (1U << 3))) { /* CSP has different short-packet rules. */
            strict_reply(s, "ERROR unsupported-bulk-out-trb\n");
            return;
        }
        capacity = trb.size & 0x00ffffffU;
        offset = segment_count == 0 ? ep->out_offset : 0;
        if (capacity > STRICT_BULK_MAX_BYTES || offset > capacity ||
            capacity > STRICT_BULK_MAX_BYTES - total_capacity) {
            strict_reply(s, "ERROR unsupported-bulk-out-length\n");
            return;
        }
        available = capacity - offset;
        bytes = MIN(remaining, available);
        chain = (ctrl & TRB_CTRL_CHN) != 0;
        last = (ctrl & TRB_CTRL_LST) != 0;
        if (remaining > available && !chain) {
            strict_reply(s, "ERROR unsupported-bulk-out-length\n");
            return;
        }
        if (!chain && !last) {
            strict_reply(s, "ERROR unsupported-bulk-out-chain\n");
            return;
        }
        StrictBulkOutSegment *segment = &segments[segment_count++];
        segment->trb = trb;
        segment->trb_pa = trb_pa;
        segment->buf_pa = ((uint64_t)trb.bph << 32) | trb.bpl;
        segment->capacity = capacity;
        segment->offset = offset;
        segment->bytes = bytes;
        segment->ctrl = ctrl;
        total_capacity += capacity;
        remaining -= bytes;
        if (remaining != 0) {
            trb_pa += sizeof(trb);
        }
    }

    bool short_packet = length < max_packet;
    StrictBulkOutSegment *last_segment = &segments[segment_count - 1];
    bool complete = short_packet ||
        ((last_segment->ctrl & TRB_CTRL_LST) != 0 &&
         last_segment->offset + last_segment->bytes ==
             last_segment->capacity);

    for (uint32_t i = 0; i < segment_count; i++) {
        StrictBulkOutSegment *segment = &segments[i];
        bool full = segment->offset + segment->bytes == segment->capacity;
        bool terminal_short = short_packet && i + 1 == segment_count;

        if (segment->bytes > 0 &&
            (!strict_ram_range(segment->buf_pa, segment->capacity) ||
             !address_space_access_valid(&address_space_memory,
                                         segment->buf_pa, segment->capacity,
                                         true, MEMTXATTRS_UNSPECIFIED) ||
             (segment->buf_pa < ring + ring_bytes &&
              ring < segment->buf_pa + segment->capacity))) {
            strict_reply(s, "ERROR payload-dma-write\n");
            return;
        }
        for (uint32_t j = 0; segment->bytes > 0 && j < segment_count; j++) {
            if (segment->buf_pa < segments[j].trb_pa + sizeof(CxdDwc3Trb) &&
                segments[j].trb_pa < segment->buf_pa + segment->capacity) {
                strict_reply(s, "ERROR bulk-out-buffer-overlaps-trb\n");
                return;
            }
        }
        if ((segment->ctrl & TRB_CTRL_IOC) && full && !terminal_short &&
            !(complete && i + 1 == segment_count &&
              (segment->ctrl & TRB_CTRL_LST))) {
            event_count++;
        }
    }
    event_count += complete ? 1U : 0U;
    if (s->gevntcount + event_count * 4U > ring_bytes) {
        strict_reply(s, "ERROR event-ring-unavailable\n");
        return;
    }

    uint32_t packet_offset = 0;
    for (uint32_t i = 0; i < segment_count; i++) {
        StrictBulkOutSegment *segment = &segments[i];
        if (segment->bytes > 0 &&
            dma_memory_write(&address_space_memory,
                             segment->buf_pa + segment->offset,
                             payload + packet_offset, segment->bytes,
                             MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
            strict_reply(s, "ERROR payload-dma-write\n");
            return;
        }
        packet_offset += segment->bytes;
    }
    for (uint32_t i = 0; i < segment_count; i++) {
        StrictBulkOutSegment *segment = &segments[i];
        uint32_t new_offset = segment->offset + segment->bytes;
        bool full = new_offset == segment->capacity;
        bool terminal_short = short_packet && i + 1 == segment_count;
        bool terminal = terminal_short ||
            (full && (segment->ctrl & TRB_CTRL_LST));

        if (full || terminal_short) {
            segment->trb.size = cpu_to_le32(segment->capacity - new_offset);
            segment->trb.ctrl = cpu_to_le32(segment->ctrl & ~TRB_CTRL_HWO);
            if (dma_memory_write(&address_space_memory, segment->trb_pa,
                                 &segment->trb, sizeof(segment->trb),
                                 MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
                strict_reply(s, "ERROR trb-dma-write\n");
                return;
            }
        }
        if (full) {
            ep->trb_addr = segment->trb_pa + sizeof(CxdDwc3Trb);
            ep->out_offset = 0;
            if ((segment->ctrl & TRB_CTRL_IOC) && !terminal) {
                dwc3_push_depevt(s, phys, DEPEVT_XFERINPROG, 0, 0);
            }
        } else {
            ep->trb_addr = segment->trb_pa;
            ep->out_offset = new_offset;
        }
        if (terminal) {
            complete = true;
        }
    }
    if (complete) {
        ep->trb_active = false;
        ep->out_offset = 0;
        dwc3_push_depevt(s, phys, DEPEVT_XFERCOMPLETE,
                         DEPEVT_STATUS_LST |
                         (short_packet ? DEPEVT_STATUS_SHORT : 0), 0);
    }
    ep->nrdy_pending = false;
    snprintf(reply, sizeof(reply), "ACK %u %u\n", logical, length);
    strict_reply(s, reply);
    return;

notify_nak:
    ep->out_offset = 0;
    if (!ep->nrdy_pending && (ep->cfg1 & (1U << 10))) {
        dwc3_push_depevt(s, phys, DEPEVT_XFERNOTREADY, 0, 0);
        ep->nrdy_pending = true;
    }
    snprintf(reply, sizeof(reply), "NAK %u\n", logical);
    strict_reply(s, reply);
}

static void strict_bulk_out(CxdDwc3State *s, uint32_t logical,
                            const uint8_t *payload, uint32_t length)
{
    CxdDwc3Ep *ep = NULL;
    CxdDwc3Trb trb;
    char reply[64];
    uint32_t phys = 0, ctrl, kind, capacity, max_packet, new_offset;
    unsigned ep_type;
    uint32_t ring_bytes = s->gevntsiz & 0xFFFCU;
    uint64_t ring = ((uint64_t)s->gevntadrhi << 32) | s->gevntadrlo;
    uint64_t address;

    if (!s->host_connected || !(s->dctl & DCTL_RUN_STOP)) {
        goto nak;
    }
    for (uint32_t i = 2; i < DWC3_MAX_EPS; i += 2) {
        if ((s->dalepena & (1U << i)) &&
            ((s->eps[i].cfg1 >> 25) & 0x1FU) == logical) {
            if (ep) {
                strict_reply(s, "ERROR ambiguous-bulk-out-endpoint\n");
                return;
            }
            ep = &s->eps[i];
            phys = i;
        }
    }
    if (!ep && logical < DWC3_MAX_EPS && (s->dalepena & (1U << logical)) &&
        ((s->eps[logical].cfg1 >> 25) & 0x1FU) == 0) {
        ep = &s->eps[logical];
        phys = logical;
    }
    if (!ep) {
        goto nak;
    }
    ep_type = (ep->cfg0 >> 1) & 3U;
    if ((ep_type != 2 && ep_type != 3) || !(ep->cfg1 & (1U << 8))) {
        strict_reply(s, "ERROR unsupported-bulk-out-endpoint\n");
        return;
    }
    if (ep->stalled) {
        snprintf(reply, sizeof(reply), "STALL %u\n", logical);
        strict_reply(s, reply);
        return;
    }
    if (!ring || ring_bytes < 4 || !strict_ram_range(ring, ring_bytes) ||
        s->gevnt_head >= ring_bytes || s->gevntcount + 4 > ring_bytes ||
        !address_space_access_valid(&address_space_memory, ring + s->gevnt_head,
                                    4, true, MEMTXATTRS_UNSPECIFIED)) {
        strict_reply(s, "ERROR event-ring-unavailable\n");
        return;
    }
    if (!ep->trb_active || !ep->trb_addr) {
        goto notify_nak;
    }
    if ((ep->trb_addr & 15U) || !strict_ram_range(ep->trb_addr, sizeof(trb)) ||
        dma_memory_read(&address_space_memory, ep->trb_addr, &trb,
                        sizeof(trb), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
        strict_reply(s, "ERROR trb-dma-read\n");
        return;
    }
    ctrl = le32_to_cpu(trb.ctrl);
    if (!(ctrl & TRB_CTRL_HWO)) {
        goto notify_nak;
    }
    kind = (ctrl >> 4) & 0x3fU;
    if (kind == TRBCTL_LINK || (ctrl & TRB_CTRL_CHN)) {
        max_packet = (ep->cfg0 >> 3) & 0x7ffU;
        if (max_packet == 0 || length > max_packet) {
            strict_reply(s, "ERROR unsupported-bulk-out-length\n");
            return;
        }
        strict_bulk_out_chained(s, ep, logical, phys, payload, length,
                                max_packet, ring_bytes, ring);
        return;
    }
    capacity = le32_to_cpu(trb.size);
    if (kind != TRBCTL_NORMAL ||
        (ctrl & (TRB_CTRL_LST | TRB_CTRL_CHN)) != TRB_CTRL_LST ||
        capacity > STRICT_BULK_MAX_BYTES) {
        strict_reply(s, "ERROR unsupported-bulk-out-trb\n");
        return;
    }
    max_packet = (ep->cfg0 >> 3) & 0x7ffU;
    if (max_packet == 0 || length > max_packet ||
        ep->out_offset > capacity || length > capacity - ep->out_offset) {
        strict_reply(s, "ERROR unsupported-bulk-out-length\n");
        return;
    }
    address = ((uint64_t)le32_to_cpu(trb.bph) << 32) | le32_to_cpu(trb.bpl);
    if (!strict_ram_range(address, capacity) ||
        (address < ep->trb_addr + sizeof(trb) && ep->trb_addr < address + capacity) ||
        (address < ring + ring_bytes && ring < address + capacity) ||
        (ep->trb_addr < ring + ring_bytes && ring < ep->trb_addr + sizeof(trb)) ||
        !address_space_access_valid(&address_space_memory, ep->trb_addr,
                                    sizeof(trb), true, MEMTXATTRS_UNSPECIFIED) ||
        (length > 0 &&
         dma_memory_write(&address_space_memory, address + ep->out_offset,
                          payload, length, MEMTXATTRS_UNSPECIFIED) != MEMTX_OK)) {
        strict_reply(s, "ERROR payload-dma-write\n");
        return;
    }
    new_offset = ep->out_offset + length;
    if (new_offset == capacity || length < max_packet) {
        trb.size = cpu_to_le32(capacity - new_offset);
        trb.ctrl = cpu_to_le32(ctrl & ~TRB_CTRL_HWO);
        if (dma_memory_write(&address_space_memory, ep->trb_addr, &trb,
                             sizeof(trb), MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
            strict_reply(s, "ERROR trb-dma-write\n");
            return;
        }
        ep->trb_active = false;
        ep->out_offset = 0;
        ep->nrdy_pending = false;
        dwc3_push_depevt(s, phys, DEPEVT_XFERCOMPLETE,
                         DEPEVT_STATUS_LST |
                         (length < max_packet ? DEPEVT_STATUS_SHORT : 0), 0);
    } else {
        ep->out_offset = new_offset;
        ep->nrdy_pending = false;
    }
    snprintf(reply, sizeof(reply), "ACK %u %u\n", logical, length);
    strict_reply(s, reply);
    return;

notify_nak:
    ep->out_offset = 0;
    if (!ep->nrdy_pending && (ep->cfg1 & (1U << 10))) {
        dwc3_push_depevt(s, phys, DEPEVT_XFERNOTREADY, 0, 0);
        ep->nrdy_pending = true;
    }
nak:
    snprintf(reply, sizeof(reply), "NAK %u\n", logical);
    strict_reply(s, reply);
}

static void strict_host_line(CxdDwc3State *s, const char *line)
{
    uint8_t data[1024];
    uint32_t length;
    if (!strncmp(line, "SETUP ", 6)) {
        if (!strict_hex(line + 6, data, &length) || length != 8) {
            strict_reply(s, "ERROR setup-encoding-or-length\n");
        } else {
            strict_token(s, 0, true, data, length);
        }
    } else if (!strcmp(line, "IN 1")) {
        strict_token(s, 1, false, NULL, 0);
    } else if (s->strict_bulk && !strncmp(line, "IN ", 3)) {
        char *end = NULL;
        unsigned long ep_idx = strtoul(line + 3, &end, 10);
        if (!end || *end != '\0' || ep_idx < 2 || ep_idx >= DWC3_MAX_EPS ||
            !dwc3_ep_is_in((uint32_t)ep_idx)) {
            strict_reply(s, "ERROR unsupported-host-command\n");
        } else {
            strict_bulk_in(s, (uint32_t)ep_idx);
        }
    } else if (!strncmp(line, "OUT 0 ", 6)) {
        if (!strcmp(line + 6, "-")) {
            strict_token(s, 0, false, NULL, 0);
        } else if (strict_hex(line + 6, data, &length) && length) {
            strict_token(s, 0, false, data, length);
        } else {
            strict_reply(s, "ERROR out-encoding\n");
        }
    } else if (s->strict_bulk && !strncmp(line, "OUT ", 4)) {
        char *end;
        unsigned long ep_idx = strtoul(line + 4, &end, 10);
        if (!g_ascii_isdigit(line[4]) || *end != ' ' ||
            ep_idx < 2 || ep_idx >= DWC3_MAX_EPS ||
            dwc3_ep_is_in((uint32_t)ep_idx)) {
            strict_reply(s, "ERROR unsupported-host-command\n");
        } else if (!strcmp(end + 1, "-")) {
            strict_bulk_out(s, (uint32_t)ep_idx, NULL, 0);
        } else if (!strict_hex(end + 1, data, &length) || !length) {
            strict_reply(s, "ERROR out-encoding\n");
        } else {
            strict_bulk_out(s, (uint32_t)ep_idx, data, length);
        }
    } else {
        strict_reply(s, "ERROR unsupported-host-command\n");
    }
}

static void dwc3_handle_host_line(CxdDwc3State *s, const char *line)
{
    if (strcmp(line, "CONN") == 0) {
        s->host_connected = true;
        s->control_stage = 0;
        s->dcfg &= ~(0x7FU << 3);
        s->eps[0].stalled = false;
        s->eps[1].stalled = false;
        for (uint32_t i = 0; i < DWC3_MAX_EPS; i++) {
            s->eps[i].out_offset = 0;
            s->eps[i].nrdy_pending = false;
        }
        if (s->dctl & DCTL_RUN_STOP) {
            dwc3_push_devt(s, DEVT_USBRESET, 0);
            dwc3_push_devt(s, DEVT_CONNECTDONE, 0);
        }
    } else if (strcmp(line, "DISC") == 0) {
        s->host_connected = false;
        for (uint32_t i = 0; i < DWC3_MAX_EPS; i++) {
            s->eps[i].out_offset = 0;
            s->eps[i].nrdy_pending = false;
        }
        if (s->dctl & DCTL_RUN_STOP) {
            dwc3_push_devt(s, DEVT_DISCONNECT, 0);
        }
    } else if (strcmp(line, "RESET") == 0) {
        if (s->host_connected) {
            s->control_stage = 0;
            s->dcfg &= ~(0x7FU << 3);
            s->eps[0].stalled = false;
            s->eps[0].out_offset = 0;
            s->eps[0].nrdy_pending = false;
            s->eps[1].stalled = false;
            s->eps[1].trb_active = false;
            s->eps[1].out_offset = 0;
            s->eps[1].nrdy_pending = false;
            for (uint32_t i = 2; i < DWC3_MAX_EPS; i++) {
                s->eps[i].stalled = false;
                s->eps[i].trb_active = false;
                s->eps[i].out_offset = 0;
                s->eps[i].nrdy_pending = false;
                s->eps[i].has_pending_out = false;
                s->eps[i].pending_out_len = 0;
            }
            if (s->dctl & DCTL_RUN_STOP) {
                dwc3_push_devt(s, DEVT_USBRESET, 0);
                dwc3_push_devt(s, DEVT_CONNECTDONE, 0);
            }
        }
    } else if (s->strict_ep0) {
        strict_host_line(s, line);
    } else if (strncmp(line, "SETUP ", 6) == 0) {
        CxdDwc3Ep *ep0 = &s->eps[0];
        size_t n = decode_hex(line + 6, ep0->pending_out, sizeof(ep0->pending_out));
        if (n > 0) {
            ep0->pending_out_len = (uint32_t)n;
            ep0->has_pending_out = true;
            dwc3_ep_process_trb(s, 0);
        }
    } else if (strncmp(line, "OUT ", 4) == 0) {
        unsigned ep_idx = 0;
        const char *p = line + 4;
        char *end = NULL;
        ep_idx = (unsigned)strtoul(p, &end, 10);
        if (end && *end == ' ' && ep_idx < DWC3_MAX_EPS && !dwc3_ep_is_in(ep_idx)) {
            CxdDwc3Ep *ep = &s->eps[ep_idx];
            size_t n = decode_hex(end + 1, ep->pending_out, sizeof(ep->pending_out));
            if (n > 0) {
                ep->pending_out_len = (uint32_t)n;
                ep->has_pending_out = true;
                dwc3_ep_process_trb(s, ep_idx);
            }
        }
    }
}

static int cxd_dwc3_chr_can_read(void *opaque)
{
    return 1024;
}

static void cxd_dwc3_chr_read(void *opaque, const uint8_t *buf, int size)
{
    CxdDwc3State *s = CXD90045_DWC3(opaque);
    for (int i = 0; i < size; i++) {
        char c = (char)buf[i];
        if (c == '\r') {
            continue;
        }
        if (c == '\n') {
            if (s->strict_ep0 && s->rx_overflow) {
                strict_reply(s, "ERROR host-line-too-long\n");
                s->rx_len = 0;
                s->rx_overflow = false;
                continue;
            }
            s->rx_line[s->rx_len] = '\0';
            if (s->rx_len > 0) {
                dwc3_handle_host_line(s, s->rx_line);
                s->rx_len = 0;
            }
        } else if (s->rx_len + 1 < sizeof(s->rx_line)) {
            s->rx_line[s->rx_len++] = c;
        } else if (s->strict_ep0) {
            s->rx_overflow = true;
        }
    }
}

static void cxd_dwc3_reset(DeviceState *dev)
{
    CxdDwc3State *s = CXD90045_DWC3(dev);
    s->gctl = 0;
    s->ggpio = 0;
    s->guid = 0;
    s->gusb2phycfg0 = 0;
    s->gusb3pipectl0 = 0;
    s->gevntadrlo = 0;
    s->gevntadrhi = 0;
    s->gevntsiz = 0;
    s->host_connected = false;
    s->rx_len = 0;
    s->rx_overflow = false;
    dwc3_device_reset(s);
}

/* E0597: fixed connected-MSC observation, not a reset/timing model. Only
 * this read-only word is supplied; the legacy unimplemented baseline stays
 * unchanged unless the captured-start-gate policy is explicitly selected. */
static uint64_t cxd_start_gate_read(void *opaque, hwaddr offset, unsigned size)
{
    return 1;
}

static bool cxd_start_gate_accepts(void *opaque, hwaddr offset, unsigned size,
                                  bool is_write, MemTxAttrs attrs)
{
    return !is_write;
}

static const MemoryRegionOps cxd_start_gate_ops = {
    .read = cxd_start_gate_read,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .accepts = cxd_start_gate_accepts },
};

static void cxd_dwc3_realize(DeviceState *dev, Error **errp)
{
    CxdDwc3State *s = CXD90045_DWC3(dev);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);

    memory_region_init_io(&s->mr, OBJECT(s), &cxd_dwc3_ops, s,
                          TYPE_CXD90045_DWC3, DWC3_MMIO_SIZE);
    sysbus_init_mmio(sbd, &s->mr);
    sysbus_init_irq(sbd, &s->irq);
    if (s->captured_start_gate) {
        memory_region_init_io(&s->captured_start_gate_mr, OBJECT(s),
                              &cxd_start_gate_ops, s, "cxd90045.captured-start-gate", 4);
        memory_region_add_subregion_overlap(get_system_memory(), 0xF0200024ULL,
                                            &s->captured_start_gate_mr, 0);
    }

    if (qemu_chr_fe_backend_connected(&s->chr)) {
        qemu_chr_fe_set_handlers(&s->chr, cxd_dwc3_chr_can_read,
                                 cxd_dwc3_chr_read, NULL, NULL, s, NULL, true);
    }
}

static const Property cxd_dwc3_props[] = {
    DEFINE_PROP_CHR("chardev", CxdDwc3State, chr),
    DEFINE_PROP_BOOL("captured-hwparams", CxdDwc3State, captured_hwparams, false),
    DEFINE_PROP_BOOL("captured-start-gate", CxdDwc3State, captured_start_gate, false),
    DEFINE_PROP_BOOL("strict-ep0", CxdDwc3State, strict_ep0, false),
    DEFINE_PROP_BOOL("strict-bulk", CxdDwc3State, strict_bulk, false),
    DEFINE_PROP_BOOL("allow-setup-abort", CxdDwc3State, allow_setup_abort, false),
};

static const VMStateDescription vmstate_cxd_dwc3_ep = {
    .name = "cxd90045-dwc3-ep",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(par2, CxdDwc3Ep),
        VMSTATE_UINT32(par1, CxdDwc3Ep),
        VMSTATE_UINT32(par0, CxdDwc3Ep),
        VMSTATE_UINT32(cmd, CxdDwc3Ep),
        VMSTATE_UINT32(cfg0, CxdDwc3Ep),
        VMSTATE_UINT32(cfg1, CxdDwc3Ep),
        VMSTATE_UINT64(trb_addr, CxdDwc3Ep),
        VMSTATE_BOOL(trb_active, CxdDwc3Ep),
        VMSTATE_BOOL(stalled, CxdDwc3Ep),
        VMSTATE_UINT8_ARRAY(pending_out, CxdDwc3Ep, 1024),
        VMSTATE_UINT32(pending_out_len, CxdDwc3Ep),
        VMSTATE_BOOL(has_pending_out, CxdDwc3Ep),
        VMSTATE_BOOL(nrdy_pending, CxdDwc3Ep),
        VMSTATE_END_OF_LIST()
    }
};

static const VMStateDescription vmstate_cxd_dwc3 = {
    .name = "cxd90045-dwc3",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(gctl, CxdDwc3State),
        VMSTATE_UINT32(ggpio, CxdDwc3State),
        VMSTATE_UINT32(guid, CxdDwc3State),
        VMSTATE_UINT32(gusb2phycfg0, CxdDwc3State),
        VMSTATE_UINT32(gusb3pipectl0, CxdDwc3State),
        VMSTATE_UINT32(gevntadrlo, CxdDwc3State),
        VMSTATE_UINT32(gevntadrhi, CxdDwc3State),
        VMSTATE_UINT32(gevntsiz, CxdDwc3State),
        VMSTATE_UINT32(gevntcount, CxdDwc3State),
        VMSTATE_UINT32(gevnt_head, CxdDwc3State),
        VMSTATE_UINT32(dcfg, CxdDwc3State),
        VMSTATE_UINT32(dctl, CxdDwc3State),
        VMSTATE_UINT32(devten, CxdDwc3State),
        VMSTATE_UINT32(dsts, CxdDwc3State),
        VMSTATE_UINT32(dgcmdpar, CxdDwc3State),
        VMSTATE_UINT32(dgcmd, CxdDwc3State),
        VMSTATE_UINT32(dalepena, CxdDwc3State),
        VMSTATE_BOOL(host_connected, CxdDwc3State),
        VMSTATE_UINT8(control_stage, CxdDwc3State),
        VMSTATE_BOOL(control_data_in, CxdDwc3State),
        VMSTATE_BOOL(control_has_data, CxdDwc3State),
        VMSTATE_STRUCT_ARRAY(eps, CxdDwc3State, DWC3_MAX_EPS, 1,
                             vmstate_cxd_dwc3_ep, CxdDwc3Ep),
        VMSTATE_END_OF_LIST()
    }
};

static void cxd_dwc3_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->vmsd = &vmstate_cxd_dwc3;
    device_class_set_legacy_reset(dc, cxd_dwc3_reset);
    dc->realize = cxd_dwc3_realize;
    device_class_set_props(dc, cxd_dwc3_props);
}

static const TypeInfo cxd_dwc3_info = {
    .name          = TYPE_CXD90045_DWC3,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(CxdDwc3State),
    .class_init    = cxd_dwc3_class_init,
};

static void cxd_dwc3_register_types(void)
{
    type_register_static(&cxd_dwc3_info);
}

type_init(cxd_dwc3_register_types)
