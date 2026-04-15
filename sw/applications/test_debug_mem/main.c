volatile int debug __attribute__((section(".xheep_debug_mem")));

void main(){
    debug = 'hey!';
    debug = 'this';
    debug = 'is';
    debug = 'a';
    debug = 'test';
    debug = 123;
    debug = 0xDEAD;
    return 0;
}