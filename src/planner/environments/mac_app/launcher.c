/* Native main executable. Preserve PID, signals, exit status, and argument boundaries. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "runtime_path.h"
extern char **environ;

int main(int argc, char **argv) {
    char **arguments = calloc((size_t)argc + 6, sizeof(char *));
    if (arguments == NULL) { perror("Panels: arguments"); return EXIT_FAILURE; }
    arguments[0] = PANELS_PYTHON;
    arguments[1] = "-I";
    arguments[2] = "-B";
    arguments[3] = "-m";
    arguments[4] = "planner.executor.entrypoint";
    for (int i = 1; i < argc; i++) { arguments[i + 4] = argv[i]; }
    execve(PANELS_PYTHON, arguments, environ);
    perror("Panels: fixed Python runtime");
    free(arguments);
    return EXIT_FAILURE;
}
