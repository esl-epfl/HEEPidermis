#ifndef DLC_SDK_H_
#define DLC_SDK_H_

#include <stdint.h>
#include <stdbool.h>
#include "dma.h"
#include "dlc.h"

/*
Generic driver for the digital Level Crossing (dLC) hardware block.
This SDK knows only about the dLC IP and the DMA.
*/

#define DLC_AMPLITUDE_BITS  2   // total bits for the delta-level field
#define DLC_TIME_BITS       6   // bits for the delta-time field

// Status codes.
typedef enum {
    DLC_STATUS_OK = 0,
    DLC_STATUS_NOT_INITIALIZED,
    DLC_STATUS_INVALID_ARGUMENT,
    DLC_STATUS_NO_EVENT,        /* ΔLvl = 0: no crossing in this packet  */
    DLC_STATUS_INVALID_EVENT,   /* ΔT   = 0: malformed or overflow event */
} dlc_status_t;

// All dLC hardware knobs in one place.
typedef struct {
    uint8_t log_level_width;    // log2 of the quantization step in source counts
    uint8_t discard_bits;       // LSBs discarded before level extraction
    uint8_t dlvl_format;        // 0 = sign-magnitude, 1 = two's complement
    uint8_t hysteresis_en;      // 1 = enable 1-level dead zone against chattering
    uint32_t initial_level;     // initial absolute dLC level
} dlc_config_t;

/*
Initialize the dLC hardware registers, configure the DMA, and launch the
acquisition pipeline.
*/
dlc_status_t dlc_init(
    const dlc_config_t      *config,          // dLC configuration (level width, format, hysteresis)
    uint8_t                 *src_ptr,         // address of the source register the DMA will read from
    dma_trigger_slot_mask_t  src_trig,        // DMA trigger slot that paces the reads (e.g. EXT_RX for VCO)
    dma_data_type_t          src_type,        // data width of one source read (e.g. HALF_WORD for VCO counter)
    uint8_t                 *results_buf,     // caller-allocated byte buffer for output events
    uint16_t                 buf_size,        // DMA output-event limit and size of results_buf in bytes
    uint32_t                 input_samples    // number of source reads per dLC transaction
);

// Write the initial quantized level into the dLC CURR_LVL register.
void dlc_set_initial_level(uint32_t level);

/*
Decode one raw 8-bit event byte into its two fields.
This is a pure bit-extraction function.
*/
dlc_status_t dlc_decode_event(uint8_t packed_event, int16_t *dlvl, uint16_t *dt);

#endif /* DLC_SDK_H_ */
