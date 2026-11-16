/*
 * Minimal macOS .app entry point — Finder often ignores shell-script executables.
 * Compile to Contents/MacOS/launch and exec the project venv Python directly.
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static const char *PROJECT_ROOT = "/Users/henrylai/myprojects/Retirement_Financial_Modeling";
static const char *PYTHON = "/Users/henrylai/myprojects/Retirement_Financial_Modeling/.venv/bin/python";
static const char *MAIN_PY = "/Users/henrylai/myprojects/Retirement_Financial_Modeling/main.py";

static void log_line(const char *message) {
    const char *home = getenv("HOME");
    if (home == NULL) {
        return;
    }
    char path[1024];
    snprintf(path, sizeof(path), "%s/Library/Application Support/RetirementPlanner/launcher.log", home);
    FILE *log = fopen(path, "a");
    if (log == NULL) {
        return;
    }
    time_t now = time(NULL);
    struct tm local;
    localtime_r(&now, &local);
    char stamp[32];
    strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &local);
    fprintf(log, "%s native_launch: %s\n", stamp, message);
    fclose(log);
}

int main(int argc, char *argv[]) {
    (void)argc;
    (void)argv;

    log_line("started");

    if (chdir(PROJECT_ROOT) != 0) {
        log_line("chdir failed");
        return 1;
    }

    setenv("PYTHONUNBUFFERED", "1", 1);
    setenv("PYTHONPATH", PROJECT_ROOT, 1);

    if (access(PYTHON, X_OK) != 0) {
        log_line("python not executable");
        return 1;
    }

    log_line("exec python main.py --web");
    execl(PYTHON, "python", MAIN_PY, "--web", (char *)NULL);

    log_line(strerror(errno));
    return 1;
}