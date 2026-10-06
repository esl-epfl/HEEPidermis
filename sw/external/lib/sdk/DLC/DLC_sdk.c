#include "DLC_sdk.h"
#include "cheep.h"

// DMA structures
static dma_target_t s_tgt_src;
static dma_target_t s_tgt_dst;
static dma_trans_t  s_trans;

// Internal decode state populated by dlc_init.
static struct {
    uint8_t  dlvl_n_bits;
    uint8_t  dlvl_format;
    uint16_t dlvl_mask;
    uint16_t dt_mask;
    bool     initialized;
} s_state;

// This function sets and configure the dLc registers. It also configure the DMA
dlc_status_t dlc_init(
    const dlc_config_t      *config,
    uint8_t                 *src_ptr,
    dma_trigger_slot_mask_t  src_trig,
    dma_data_type_t          src_type,
    uint8_t                 *results_buf,
    uint16_t                 buf_size,
    uint32_t                 input_samples
) {
    if (!config || !src_ptr || !results_buf || buf_size == 0 || input_samples == 0) {
        return DLC_STATUS_INVALID_ARGUMENT;
    }
    uint8_t time_bits = config->time_bits ? config->time_bits : DLC_TIME_BITS;
    if (time_bits < 1U || time_bits > 6U || config->discard_bits > 15U ||
        config->log_level_width > 15U || config->dlvl_format > 1U)
        return DLC_STATUS_INVALID_ARGUMENT;
    uint8_t amplitude_bits = 8U - time_bits;

    // Program the dLC registers
    volatile uint32_t *dlvl_log_level_width = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_DLVL_LOG_LEVEL_WIDTH_REG_OFFSET);
    volatile uint32_t *dlvl_n_bits_reg      = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_DLVL_N_BITS_REG_OFFSET);
    volatile uint32_t *dlvl_format_reg      = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_DLVL_FORMAT_REG_OFFSET);
    volatile uint32_t *dlvl_mask_reg        = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_DLVL_MASK_REG_OFFSET);
    volatile uint32_t *dt_mask_reg          = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_DT_MASK_REG_OFFSET);
    volatile uint32_t *dlc_size_reg         = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_TRANS_SIZE_REG_OFFSET);
    volatile uint32_t *dlc_hysteresis_en    = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_HYSTERESIS_EN_REG_OFFSET);
    volatile uint32_t *dlc_discard_bits     = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_DISCARD_BITS_REG_OFFSET);

    // In sign-magnitude one bit is used for the sign, in two's complement all fields are used
    uint8_t n_bits_mag = config->dlvl_format
                         ? amplitude_bits
                         : (amplitude_bits - 1);

    // Set the register values based on configuration
    *dlvl_format_reg      = config->dlvl_format;
    *dlvl_log_level_width = config->log_level_width;
    *dlvl_n_bits_reg      = n_bits_mag;
    *dlvl_mask_reg        = (1u << n_bits_mag) - 1u;
    *dt_mask_reg          = (1u << time_bits) - 1u;
    *dlc_hysteresis_en    = config->hysteresis_en;
    *dlc_discard_bits     = config->discard_bits;
    *dlc_size_reg         = input_samples;

    // Save the configured state for decode later on
    s_state.dlvl_n_bits  = n_bits_mag;
    s_state.dlvl_format  = config->dlvl_format;
    s_state.dlvl_mask    = (uint16_t)((1u << n_bits_mag) - 1u);
    s_state.dt_mask      = (uint16_t)((1u << time_bits) - 1u);

    // Configure and launch the DMA
    dma_init(NULL);

    s_tgt_src.ptr       = src_ptr;
    s_tgt_src.trig      = src_trig;
    s_tgt_src.inc_d1_du = 0;           /* always read the same register */
    s_tgt_src.type      = src_type;

    s_tgt_dst.ptr       = results_buf;
    s_tgt_dst.trig      = DMA_TRIG_MEMORY;
    s_tgt_dst.inc_d1_du = 1;           /* advance one byte per event    */
    s_tgt_dst.type      = config->halfword_output ? DMA_DATA_TYPE_HALF_WORD : DMA_DATA_TYPE_BYTE;

    s_trans.src        = &s_tgt_src;
    s_trans.dst        = &s_tgt_dst;
    s_trans.dim        = DMA_DIM_CONF_1D;
    s_trans.channel    = 0;
    s_trans.size_d1_du = input_samples;
    s_trans.win_du     = config->single_shot ? 0U : buf_size;
    s_trans.end        = config->single_shot ? DMA_TRANS_END_POLLING : DMA_TRANS_END_INTR;
    s_trans.mode       = config->single_shot ? DMA_TRANS_MODE_SINGLE : DMA_TRANS_MODE_CIRCULAR;
    s_trans.hw_fifo_en = true;

    dma_config_flags_t res;
    res = dma_validate_transaction(&s_trans, DMA_ENABLE_REALIGN, DMA_PERFORM_CHECKS_INTEGRITY);
    if (res != DMA_CONFIG_OK) return DLC_STATUS_NOT_INITIALIZED;

    res = dma_load_transaction(&s_trans);
    if (res != DMA_CONFIG_OK) return DLC_STATUS_NOT_INITIALIZED;

    if (dma_launch(&s_trans) != DMA_CONFIG_OK) return DLC_STATUS_NOT_INITIALIZED;

    s_state.initialized = true;
    return DLC_STATUS_OK;
}

dlc_status_t dlc_start_transaction(void) {
    if (!s_state.initialized) return DLC_STATUS_NOT_INITIALIZED;
    if (dma_load_transaction(&s_trans) != DMA_CONFIG_OK || dma_launch(&s_trans) != DMA_CONFIG_OK)
        return DLC_STATUS_NOT_INITIALIZED;
    return DLC_STATUS_OK;
}

// This function is used to set the baseline initial level of the dLc
void dlc_set_initial_level(uint32_t level) {
    volatile uint32_t *dlc_init_level = (volatile uint32_t *)(DLC_START_ADDRESS + DLC_CURR_LVL_REG_OFFSET);
    *dlc_init_level = level;
}


dlc_status_t dlc_decode_event(uint8_t packed_event, int16_t *dlvl, uint16_t *dt) {
    if (!s_state.initialized)  return DLC_STATUS_NOT_INITIALIZED;
    if (!dlvl || !dt)          return DLC_STATUS_INVALID_ARGUMENT;

    if (s_state.dlvl_format == 0) {
        // RTL packs sign-magnitude events as {dt, sign, magnitude}.
        uint16_t mag  = (uint16_t)(packed_event & s_state.dlvl_mask);
        uint8_t  sign = (packed_event >> s_state.dlvl_n_bits) & 1u;
        *dt = (uint16_t)((packed_event >> (s_state.dlvl_n_bits + 1u)) & s_state.dt_mask);
        *dlvl = sign ? -(int16_t)mag : (int16_t)mag;
    } else {
        // RTL packs two's-complement events as {dt, delta-level}.
        uint8_t raw = packed_event & ((1u << s_state.dlvl_n_bits) - 1u);
        *dt = (uint16_t)((packed_event >> s_state.dlvl_n_bits) & s_state.dt_mask);
        if (raw & (1u << (s_state.dlvl_n_bits - 1u)))
            raw |= (uint8_t)(~((1u << s_state.dlvl_n_bits) - 1u));
        *dlvl = (int16_t)(int8_t)raw;
    }

    if (*dlvl == 0) return DLC_STATUS_NO_EVENT;

    return DLC_STATUS_OK;
}
