# Native host build for raw dLC feature extraction.

CC      = gcc
CFLAGS  = -O2 -Wall -Wextra -std=c11
LDFLAGS = -lm

TARGET  = raw_dlc_fe_native
SRCS    = raw_dlc_fe_native.c
OUT_CSV = raw_dlc_fe_native_output.csv

.PHONY: all run clean

all: $(TARGET)

$(TARGET): $(SRCS)
	$(CC) $(CFLAGS) -o $@ $(SRCS) $(LDFLAGS)

run: $(TARGET)
	./$(TARGET) --input sim.txt --method delta > $(OUT_CSV)
	@echo "Raw dLC FE output saved to $(OUT_CSV)"
	@wc -l $(OUT_CSV)

clean:
	rm -f $(TARGET) $(OUT_CSV)
