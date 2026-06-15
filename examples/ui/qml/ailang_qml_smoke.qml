import QtQuick
import QtQuick.Controls

Window {
    id: root
    width: 720
    height: 420
    visible: true
    title: "AILang QML smoke"
    color: "#111827"

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#172033" }
            GradientStop { position: 1.0; color: "#0b1020" }
        }
    }

    Rectangle {
        width: 460
        height: 220
        anchors.centerIn: parent
        radius: 18
        color: "#f8fafc"
        border.color: "#cbd5e1"

        Column {
            anchors.fill: parent
            anchors.margins: 28
            spacing: 14

            Text {
                text: "AILang QML bridge"
                font.pixelSize: 30
                font.bold: true
                color: "#0f172a"
            }

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Generic Qt/QML host smoke scene. Product-specific shells live outside the AILang repository."
                font.pixelSize: 15
                color: "#475569"
            }

            Rectangle {
                width: 178
                height: 42
                radius: 10
                color: "#2563eb"

                Text {
                    anchors.centerIn: parent
                    text: "runtime ready"
                    font.pixelSize: 14
                    font.bold: true
                    color: "#ffffff"
                }
            }
        }
    }
}
