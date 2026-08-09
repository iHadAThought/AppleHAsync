/*
 * In-process Python launcher so TCC / Login Items attribute to appleHAsync.app
 * (not "Python" / Terminal). EventKit calls stay in this Mach-O image.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <mach-o/dyld.h>

static int get_executable_path(char *out, size_t out_size) {
    uint32_t size = (uint32_t)out_size;
    if (_NSGetExecutablePath(out, &size) != 0) {
        return -1;
    }
    char resolved[PATH_MAX];
    if (realpath(out, resolved) != NULL) {
        strncpy(out, resolved, out_size - 1);
        out[out_size - 1] = '\0';
    }
    return 0;
}

static int dirname_copy(const char *path, char *out, size_t out_size) {
    const char *slash = strrchr(path, '/');
    if (!slash || slash == path) {
        return -1;
    }
    size_t n = (size_t)(slash - path);
    if (n >= out_size) {
        return -1;
    }
    memcpy(out, path, n);
    out[n] = '\0';
    return 0;
}

/* Load KEY=VALUE lines from Contents/Resources/runtime.env into the environment. */
static void load_runtime_env(const char *contents_dir) {
    char path[PATH_MAX];
    snprintf(path, sizeof(path), "%s/Resources/runtime.env", contents_dir);
    FILE *fp = fopen(path, "r");
    if (!fp) {
        return;
    }
    char line[PATH_MAX * 2];
    while (fgets(line, sizeof(line), fp)) {
        char *nl = strchr(line, '\n');
        if (nl) {
            *nl = '\0';
        }
        if (line[0] == '#' || line[0] == '\0') {
            continue;
        }
        char *eq = strchr(line, '=');
        if (!eq) {
            continue;
        }
        *eq = '\0';
        /* Do not clobber LaunchAgent / caller env for root/data; always apply site path. */
        int overwrite = (strcmp(line, "APPLE_HASYNC_SITE_PACKAGES") == 0) ? 1 : 0;
        setenv(line, eq + 1, overwrite);
    }
    fclose(fp);
}

int main(int argc, char **argv) {
    char exe[PATH_MAX];
    char macos_dir[PATH_MAX];
    char contents_dir[PATH_MAX];
    char app_dir[PATH_MAX];
    char root[PATH_MAX];
    char data_dir[PATH_MAX];
    char pythonpath[PATH_MAX * 3];

    if (get_executable_path(exe, sizeof(exe)) != 0) {
        fprintf(stderr, "appleHAsync: cannot resolve executable path\n");
        return 1;
    }
    if (dirname_copy(exe, macos_dir, sizeof(macos_dir)) != 0 ||
        dirname_copy(macos_dir, contents_dir, sizeof(contents_dir)) != 0 ||
        dirname_copy(contents_dir, app_dir, sizeof(app_dir)) != 0) {
        fprintf(stderr, "appleHAsync: unexpected app layout\n");
        return 1;
    }

    load_runtime_env(contents_dir);

    const char *env_root = getenv("APPLE_HASYNC_ROOT");
    if (env_root && env_root[0]) {
        strncpy(root, env_root, sizeof(root) - 1);
        root[sizeof(root) - 1] = '\0';
    } else {
        const char *home = getenv("HOME");
        if (!home) {
            home = "";
        }
        snprintf(root, sizeof(root), "%s/appleHAsync", home);
        if (access(root, F_OK) != 0) {
            if (dirname_copy(app_dir, root, sizeof(root)) != 0) {
                fprintf(stderr, "appleHAsync: set APPLE_HASYNC_ROOT\n");
                return 1;
            }
        }
    }

    const char *env_data = getenv("APPLE_HASYNC_DATA_DIR");
    if (env_data && env_data[0]) {
        strncpy(data_dir, env_data, sizeof(data_dir) - 1);
        data_dir[sizeof(data_dir) - 1] = '\0';
    } else {
        const char *home = getenv("HOME");
        if (!home) {
            home = "";
        }
        snprintf(
            data_dir,
            sizeof(data_dir),
            "%s/Library/Application Support/appleHAsync",
            home
        );
    }

    setenv("APPLE_HASYNC_ROOT", root, 1);
    setenv("APPLE_HASYNC_DATA_DIR", data_dir, 1);
    setenv("PYTHONUNBUFFERED", "1", 1);

    char venv_home[PATH_MAX];
    snprintf(venv_home, sizeof(venv_home), "%s/.venv", root);
    if (access(venv_home, F_OK) == 0) {
        setenv("VIRTUAL_ENV", venv_home, 1);
    }

    const char *site = getenv("APPLE_HASYNC_SITE_PACKAGES");
    if (site && site[0]) {
        snprintf(pythonpath, sizeof(pythonpath), "%s:%s", root, site);
    } else {
        strncpy(pythonpath, root, sizeof(pythonpath) - 1);
        pythonpath[sizeof(pythonpath) - 1] = '\0';
    }
    setenv("PYTHONPATH", pythonpath, 1);

    /* argv: <exe> -m mac_agent.cli [user args...]  (default user arg: serve) */
    const int user_argc = (argc > 1) ? (argc - 1) : 1;
    const int nargv = 3 + user_argc;
    wchar_t **wargv = (wchar_t **)PyMem_RawMalloc(sizeof(wchar_t *) * (size_t)(nargv + 1));
    if (!wargv) {
        fprintf(stderr, "appleHAsync: out of memory\n");
        return 1;
    }

    wargv[0] = Py_DecodeLocale(exe, NULL);
    wargv[1] = Py_DecodeLocale("-m", NULL);
    wargv[2] = Py_DecodeLocale("mac_agent.cli", NULL);
    if (argc > 1) {
        for (int i = 1; i < argc; i++) {
            wargv[2 + i] = Py_DecodeLocale(argv[i], NULL);
        }
    } else {
        wargv[3] = Py_DecodeLocale("serve", NULL);
    }

    for (int i = 0; i < nargv; i++) {
        if (!wargv[i]) {
            fprintf(stderr, "appleHAsync: locale decode failed\n");
            return 1;
        }
    }
    wargv[nargv] = NULL;

    return Py_Main(nargv, wargv);
}
