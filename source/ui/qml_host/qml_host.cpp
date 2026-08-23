#include "qml_host.h"

#include <QByteArray>
#include <QCoreApplication>
#include <QFileInfo>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QTimer>
#include <QUrl>
#include <QVariant>

#include <cstdlib>
#include <memory>
#include <mutex>
#include <unordered_map>

namespace {

constexpr const char *kDefaultScene = "examples/ui/qml/ailang_qml_smoke.qml";
constexpr const char *kEnvScene = "AILANG_QML_SCENE";
constexpr const char *kEnvQuitAfterMs = "AILANG_QML_QUIT_AFTER_MS";
constexpr const char *kContextRuntime = "ailangRuntime";
constexpr const char *kContextRuntimeValue = "AILang";
constexpr const char *kContextSessionUser = "ailangSessionUser";
constexpr const char *kContextSessionUserValue = "AILang";
constexpr const char *kContextPreviewMode = "ailangPreviewMode";

class AilQmlHost {
public:
    AilQmlHost()
        : m_argv{m_arg0, nullptr},
          m_app(m_argc, m_argv),
          m_engine(std::make_unique<QQmlApplicationEngine>())
    {
    }

    int64_t loadFile(const char *path)
    {
        if (!path || !*path) {
            setError("empty QML path");
            return 0;
        }

        const QString raw = QString::fromLocal8Bit(path);
        const QUrl url = raw.startsWith(QStringLiteral("qrc:/")) ||
                                 raw.startsWith(QStringLiteral("file:/"))
                             ? QUrl(raw)
                             : QUrl::fromLocalFile(QFileInfo(raw).absoluteFilePath());
        if (url.isLocalFile())
            m_engine->addImportPath(QFileInfo(url.toLocalFile()).absolutePath());

        bool objectCreated = false;
        QObject::connect(
            m_engine.get(),
            &QQmlApplicationEngine::objectCreated,
            m_engine.get(),
            [&objectCreated, url](QObject *object, const QUrl &objectUrl) {
                if (objectUrl == url)
                    objectCreated = object != nullptr;
            });

        m_engine->load(url);
        if (!objectCreated || m_engine->rootObjects().isEmpty()) {
            setError("QML load failed");
            return 0;
        }

        clearError();
        return 1;
    }

    int64_t setContextString(const char *name, const char *value)
    {
        if (!name || !*name) {
            setError("empty context property name");
            return 0;
        }
        m_engine->rootContext()->setContextProperty(
            QString::fromLocal8Bit(name),
            QString::fromLocal8Bit(value ? value : ""));
        clearError();
        return 1;
    }

    int64_t setContextInt(const char *name, int64_t value)
    {
        if (!name || !*name) {
            setError("empty context property name");
            return 0;
        }
        m_engine->rootContext()->setContextProperty(
            QString::fromLocal8Bit(name),
            QVariant::fromValue<qlonglong>(value));
        clearError();
        return 1;
    }

    int64_t exec()
    {
        const int timeoutMs = autoQuitTimeoutMs();
        if (timeoutMs > 0)
            QTimer::singleShot(timeoutMs, &m_app, &QCoreApplication::quit);
        return static_cast<int64_t>(m_app.exec());
    }

    const char *lastError() const
    {
        return m_error.constData();
    }

private:
    int autoQuitTimeoutMs() const
    {
        const char *raw = std::getenv(kEnvQuitAfterMs);
        if (!raw || !*raw)
            return 0;
        char *end = nullptr;
        const long parsed = std::strtol(raw, &end, 10);
        if (end == raw || parsed <= 0 || parsed > 600000)
            return 0;
        return static_cast<int>(parsed);
    }

    void setError(const char *message)
    {
        m_error = message ? message : "unknown QML host error";
    }

    void clearError()
    {
        m_error.clear();
    }

    int m_argc = 1;
    char m_arg0[16] = "ailang-qml";
    char *m_argv[2];
    QGuiApplication m_app;
    std::unique_ptr<QQmlApplicationEngine> m_engine;
    QByteArray m_error;
};

std::mutex g_hostsMutex;
std::unordered_map<int64_t, std::unique_ptr<AilQmlHost>> g_hosts;
int64_t g_nextHostId = 1;
bool g_oneShotActive = false;
thread_local QByteArray g_missingHostError = "invalid QML host";

AilQmlHost *hostById(int64_t hostId)
{
    const auto it = g_hosts.find(hostId);
    if (it == g_hosts.end())
        return nullptr;
    return it->second.get();
}

const char *defaultSceneFromEnv()
{
    const char *scene = std::getenv(kEnvScene);
    return scene && *scene ? scene : kDefaultScene;
}

int64_t configureDefaultContext(AilQmlHost &host)
{
    if (host.setContextString(kContextRuntime, kContextRuntimeValue) == 0)
        return 0;
    if (host.setContextString(kContextSessionUser, kContextSessionUserValue) == 0)
        return 0;
    if (host.setContextInt(kContextPreviewMode, 1) == 0)
        return 0;
    return 1;
}

bool acquireOneShotHost()
{
    std::lock_guard<std::mutex> lock(g_hostsMutex);
    if (!g_hosts.empty() || g_oneShotActive) {
        g_missingHostError = "only one QML host can be active per process";
        return false;
    }
    g_oneShotActive = true;
    return true;
}

void releaseOneShotHost()
{
    std::lock_guard<std::mutex> lock(g_hostsMutex);
    g_oneShotActive = false;
}

int64_t runOneShotHost(const char *path, bool defaultContext)
{
    if (!acquireOneShotHost())
        return 1;

    AilQmlHost host;
    if (defaultContext && configureDefaultContext(host) == 0) {
        g_missingHostError = host.lastError();
        releaseOneShotHost();
        return 2;
    }
    if (host.loadFile(path) == 0) {
        g_missingHostError = host.lastError();
        releaseOneShotHost();
        return 3;
    }

    const int64_t rc = host.exec();
    releaseOneShotHost();
    return rc;
}

} // namespace

extern "C" int64_t ail_qml_create(void)
{
    std::lock_guard<std::mutex> lock(g_hostsMutex);
    if (!g_hosts.empty() || g_oneShotActive) {
        g_missingHostError = "only one QML host can be active per process";
        return 0;
    }
    const int64_t id = g_nextHostId++;
    g_hosts.emplace(id, std::make_unique<AilQmlHost>());
    return id;
}

extern "C" int64_t ail_qml_load_file(int64_t host_id, const char *path)
{
    std::lock_guard<std::mutex> lock(g_hostsMutex);
    AilQmlHost *host = hostById(host_id);
    return host ? host->loadFile(path) : 0;
}

extern "C" int64_t ail_qml_set_context_string(
    int64_t host_id,
    const char *name,
    const char *value)
{
    std::lock_guard<std::mutex> lock(g_hostsMutex);
    AilQmlHost *host = hostById(host_id);
    return host ? host->setContextString(name, value) : 0;
}

extern "C" int64_t ail_qml_set_context_int(
    int64_t host_id,
    const char *name,
    int64_t value)
{
    std::lock_guard<std::mutex> lock(g_hostsMutex);
    AilQmlHost *host = hostById(host_id);
    return host ? host->setContextInt(name, value) : 0;
}

extern "C" int64_t ail_qml_exec(int64_t host_id)
{
    AilQmlHost *host = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_hostsMutex);
        host = hostById(host_id);
    }
    return host ? host->exec() : 1;
}

extern "C" void ail_qml_destroy(int64_t host_id)
{
    std::lock_guard<std::mutex> lock(g_hostsMutex);
    g_hosts.erase(host_id);
}

extern "C" const char *ail_qml_last_error(int64_t host_id)
{
    std::lock_guard<std::mutex> lock(g_hostsMutex);
    AilQmlHost *host = hostById(host_id);
    return host ? host->lastError() : g_missingHostError.constData();
}

extern "C" int64_t ail_qml_run_file(const char *path)
{
    return runOneShotHost(path, false);
}

extern "C" int64_t ail_qml_run_default(void)
{
    return runOneShotHost(defaultSceneFromEnv(), true);
}
