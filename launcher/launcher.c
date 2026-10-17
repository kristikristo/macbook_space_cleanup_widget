#include <stdio.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s program [args...]\n", argv[0]);
        return 64;
    }
    execv(argv[1], argv + 1);
    perror(argv[1]);
    return 127;
}
