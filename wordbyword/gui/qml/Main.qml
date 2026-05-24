import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 1280
    height: 820
    minimumWidth: 1080
    minimumHeight: 700
    visible: true
    title: "wordbyword"
    color: "#f7f7f4"

    property int currentSection: 0
    property string uiFontFamily: (typeof guiUiFontFamily === "undefined" || guiUiFontFamily.length === 0) ? "Pretendard Variable" : guiUiFontFamily
    property string monoFontFamily: (typeof guiMonoFontFamily === "undefined" || guiMonoFontFamily.length === 0) ? "JetBrains Mono" : guiMonoFontFamily
    property int fontSizeBadge: 10
    property int fontSizeTiny: 11
    property int fontSizeLabel: 12
    property int fontSizeBody: 13
    property int fontSizePurpose: 14
    property int fontSizeBrand: 22
    property int fontSizeSection: 28
    property int fontWeightRegular: Font.Normal
    property int fontWeightMedium: Font.Medium
    property int fontWeightSemibold: Font.DemiBold
    property int fontWeightBold: Font.Bold
    property var backend: (typeof bridge === "undefined" || bridge === null) ? null : bridge
    property var safeLanguageState: backend ? backend.languageState : ({
        "selected": "system",
        "resolved": "en",
        "options": [
            {"value": "system", "label": "System"},
            {"value": "en", "label": "English"},
            {"value": "ko", "label": "Korean"}
        ]
    })
    property var textMap: backend ? backend.texts : ({})
    property var defaultJobState: ({
        "busy": false,
        "workflow": "",
        "phase": "idle",
        "detail": root.tr("app.ready_detail"),
        "status": "idle",
        "current": 0,
        "total": 0,
        "error": ""
    })
    property var safeSettings: backend ? backend.settings : ({})
    property var safeJobState: backend ? backend.jobState : defaultJobState
    property string safeLogText: backend ? backend.logText : ""
    property string safeArtifactText: backend ? backend.artifactText : root.tr("run.no_artifacts")
    property bool busy: Boolean(safeJobState.busy)
    property bool runDrawerOpen: false
    property var definitions: backend ? backend.workflowDefinitions : []
    property var readinessMap: backend ? backend.workflowReadiness : ({})
    property var currentDefinition: definitions.length > currentSection ? definitions[currentSection] : ({})
    property var currentReadiness: readinessMap[currentDefinition.key] || ({})
    property var gradeOptions: [
        {"value": "grade1", "label": "초1 / grade1"},
        {"value": "grade2", "label": "초2 / grade2"},
        {"value": "grade3", "label": "초3 / grade3"},
        {"value": "grade4", "label": "초4 / grade4"},
        {"value": "grade5", "label": "초5 / grade5"},
        {"value": "grade6", "label": "초6 / grade6"},
        {"value": "grade7", "label": "중1 / grade7"},
        {"value": "grade8", "label": "중2 / grade8"},
        {"value": "grade9", "label": "중3 / grade9"},
        {"value": "grade10", "label": "고1 / grade10"},
        {"value": "grade11", "label": "고2 / grade11"},
        {"value": "grade12", "label": "고3 / grade12"}
    ]

    function tr(key) {
        var value = textMap[key]
        return value === undefined || value === null || String(value).length === 0 ? String(key) : String(value)
    }

    function commonValues() {
        return {
            "target_grade": root.selectedTargetGrade(),
            "model": modelField.text,
            "base_url": baseUrlField.text
        }
    }

    function setupValues() {
        var values = commonValues()
        values.dictionary_input_dir = setupDictionaryInput.text
        values.class_source_dir = setupClassSource.text
        return values
    }

    function dictionaryValues() {
        var values = commonValues()
        values.dictionary_input_dir = dictionaryInput.text
        values.dictionary_output_dir = dictionaryOutput.text
        values.reference_dictionary_path = referenceDictionary.text
        values.dictionary_force = dictionaryForce.checked
        values.include_numbers = dictionaryNumbers.checked
        values.include_punctuation = dictionaryPunctuation.checked
        return values
    }

    function classPrintoutValues() {
        var values = commonValues()
        values.dictionary_output_dir = dictionaryOutput.text
        values.live_lexicon_path = liveLexicon.text
        values.class_source_dir = classSource.text
        values.source_output_root = parsedRoot.text
        values.rewrite_output_root = rewriteRoot.text
        values.print_output_root = printRoot.text
        values.rewrite_dictionary_path = rewriteDictionary.text
        values.force_class_printouts = forcePrintouts.checked
        values.skip_pdf_parse = skipPdfParse.checked
        values.pdf_render_dpi = pdfDpi.text
        values.print_font_path = printFont.text
        values.print_body_font_size = bodyFontSize.text
        return values
    }

    function archiveValues() {
        var values = commonValues()
        values.archive_root = archiveRoot.text
        values.usage_signal_output_root = usageOutputRoot.text
        values.canonical_dictionary_path = canonicalDictionary.text
        values.usage_candidates_path = usageCandidates.text
        values.usage_summary_path = usageSummary.text
        values.live_lexicon_path = liveLexicon.text
        values.supplemental_lexicon_path = supplementalLexicon.text
        values.unmatched_usage_output_path = unmatchedUsage.text
        values.include_near_duplicates = includeNearDuplicates.checked
        values.include_numbers = usageNumbers.checked
        values.include_punctuation = usagePunctuation.checked
        return values
    }

    function reportValues() {
        var values = commonValues()
        values.after_class_printout_path = reportPrintout.text
        values.after_class_report_output_root = reportOutputRoot.text
        values.after_class_report_font_path = reportFont.text
        return values
    }

    function workflowValues() {
        if (currentSection === 0)
            return setupValues()
        if (currentSection === 1)
            return dictionaryValues()
        if (currentSection === 2)
            return classPrintoutValues()
        if (currentSection === 3)
            return archiveValues()
        return reportValues()
    }

    function runWorkflow(workflowId) {
        var values = workflowValues()
        if (!workflowId || workflowId.length === 0) {
            saveSettings(values)
            return
        }
        runJob(workflowId, values)
    }

    function saveSettings(values) {
        if (!backend)
            return false
        return backend.saveSettings(values)
    }

    function runJob(workflowId, values) {
        if (!backend)
            return false
        return backend.runJob(workflowId, values)
    }

    function setLanguage(value) {
        if (!backend)
            return false
        return backend.setLanguage(value)
    }

    function languageOptionIndex(value) {
        var options = safeLanguageState.options || []
        for (var i = 0; i < options.length; i++) {
            if (String(options[i].value || "") === value)
                return i
        }
        return 0
    }

    function gradeOptionIndex(value) {
        for (var i = 0; i < gradeOptions.length; i++) {
            if (String(gradeOptions[i].value || "") === value)
                return i
        }
        return 0
    }

    function selectedTargetGrade() {
        var index = targetGradeCombo.currentIndex
        if (index >= 0 && index < gradeOptions.length)
            return String(gradeOptions[index].value || "grade1")
        return String(root.safeSettings.target_grade || "grade1")
    }

    function choosePath(current, directory) {
        if (!backend)
            return current
        return directory ? backend.chooseDirectory(current) : backend.chooseFile(current)
    }

    function backendPathForKey(key) {
        if (!backend)
            return ""
        return backend.pathForKey(key)
    }

    function iconSource(iconName, inverted) {
        if (!iconName || iconName.length === 0)
            return ""
        return "../assets/icons/" + (inverted ? "white" : "black") + "/" + iconName + ".svg"
    }

    function actionIcon(workflowId) {
        if (!workflowId || workflowId.length === 0)
            return "list-checks"
        return "play"
    }

    function statusLabel(status) {
        if (status === "missing_input")
            return root.tr("status.missing_input")
        if (status === "done")
            return root.tr("status.done")
        if (status === "running")
            return root.tr("status.running")
        if (status === "failed")
            return root.tr("status.failed")
        if (status === "idle")
            return root.tr("status.idle")
        return root.tr("status.ready")
    }

    function statusTone(status) {
        if (status === "done")
            return "#111111"
        if (status === "running")
            return "#333333"
        if (status === "failed" || status === "missing_input")
            return "#ffffff"
        return "#f7f7f4"
    }

    function statusInk(status) {
        if (status === "done" || status === "running")
            return "#ffffff"
        return "#111111"
    }

    function pathForStageKey(key) {
        if (!key)
            return ""
        if (key === "dictionary_input_dir")
            return dictionaryInput.text
        if (key === "dictionary_output_dir")
            return dictionaryOutput.text
        if (key === "reference_dictionary_path")
            return referenceDictionary.text
        if (key === "dictionary_final")
            return dictionaryOutput.text + "/15_final_dictionary.jsonl"
        if (key === "class_source_dir")
            return classSource.text
        if (key === "source_output_root")
            return parsedRoot.text
        if (key === "rewrite_output_root")
            return rewriteRoot.text
        if (key === "print_output_root")
            return printRoot.text
        if (key === "vocabulary_source")
            return rewriteDictionary.text || liveLexicon.text || dictionaryOutput.text + "/15_final_dictionary.jsonl"
        if (key === "archive_root")
            return archiveRoot.text
        if (key === "usage_signal_output_root")
            return usageOutputRoot.text
        if (key === "live_lexicon_path")
            return liveLexicon.text
        if (key === "after_class_printout_path")
            return reportPrintout.text
        if (key === "after_class_report_output_root")
            return reportOutputRoot.text
        if (key === "model")
            return modelField.text
        return backendPathForKey(key)
    }

    component LabelText: Text {
        color: "#111111"
        font.family: root.uiFontFamily
        font.pixelSize: root.fontSizeLabel
        font.weight: root.fontWeightBold
    }

    component BodyText: Text {
        color: "#111111"
        font.family: root.uiFontFamily
        font.pixelSize: root.fontSizeBody
        font.weight: root.fontWeightRegular
        wrapMode: Text.WordWrap
        lineHeight: 1.15
    }

    component WIcon: Image {
        id: icon
        property string iconName: ""
        property bool inverted: false
        property int iconSize: 18
        visible: iconName.length > 0
        source: root.iconSource(iconName, inverted)
        fillMode: Image.PreserveAspectFit
        sourceSize.width: iconSize
        sourceSize.height: iconSize
        width: iconSize
        height: iconSize
        Layout.preferredWidth: iconSize
        Layout.preferredHeight: iconSize
    }

    component WTextField: TextField {
        id: control
        color: "#111111"
        selectedTextColor: "#ffffff"
        selectionColor: "#111111"
        font.family: root.uiFontFamily
        font.pixelSize: root.fontSizeBody
        font.weight: root.fontWeightRegular
        padding: 9
        background: Rectangle {
            color: "#ffffff"
            border.color: "#111111"
            border.width: 1
            radius: 0
        }
    }

    component WButton: Button {
        id: control
        property bool primary: false
        property string iconName: ""
        property bool iconOnly: false
        property string toolTipText: ""
        Layout.preferredHeight: 38
        enabled: !root.busy
        font.family: root.uiFontFamily
        font.pixelSize: root.fontSizeLabel
        font.weight: root.fontWeightBold
        ToolTip.text: toolTipText
        ToolTip.visible: hovered && toolTipText.length > 0
        ToolTip.delay: 600
        background: Rectangle {
            color: control.down || control.primary ? "#111111" : (control.enabled ? "#ffffff" : "#e7e7e2")
            border.color: "#111111"
            border.width: control.primary ? 2 : 1
            radius: 0
        }
        contentItem: RowLayout {
            anchors.fill: parent
            anchors.margins: control.iconOnly ? 9 : 10
            spacing: control.iconOnly ? 0 : 8
            Item {
                visible: control.iconOnly
                Layout.fillWidth: true
            }
            WIcon {
                iconName: control.iconName
                inverted: control.down || control.primary
                Layout.alignment: Qt.AlignCenter
            }
            Text {
                visible: !control.iconOnly
                text: control.text
                color: control.down || control.primary ? "#ffffff" : "#111111"
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                font: control.font
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Item {
                visible: control.iconOnly
                Layout.fillWidth: true
            }
        }
    }

    component WCheckBox: CheckBox {
        id: control
        spacing: 10
        font.family: root.uiFontFamily
        font.pixelSize: root.fontSizeBody
        font.weight: root.fontWeightRegular
        indicator: Rectangle {
            implicitWidth: 18
            implicitHeight: 18
            x: control.leftPadding
            y: parent.height / 2 - height / 2
            color: "#ffffff"
            border.color: "#111111"
            border.width: 1
            radius: 0
            Text {
                anchors.centerIn: parent
                text: control.checked ? "X" : ""
                color: "#111111"
                font.family: root.uiFontFamily
                font.pixelSize: root.fontSizeLabel
                font.weight: root.fontWeightBold
            }
        }
        contentItem: Text {
            text: control.text
            color: "#111111"
            font: control.font
            verticalAlignment: Text.AlignVCenter
            leftPadding: control.indicator.width + control.spacing
        }
    }

    component StatusPill: Rectangle {
        id: pill
        property string status: "ready"
        property string label: root.statusLabel(status)
        implicitWidth: pillText.implicitWidth + 18
        implicitHeight: 24
        color: root.statusTone(status)
        border.color: "#111111"
        border.width: 1
        radius: 0
        Text {
            id: pillText
            anchors.centerIn: parent
            text: pill.label
            color: root.statusInk(pill.status)
            font.family: root.uiFontFamily
            font.pixelSize: root.fontSizeBadge
            font.weight: root.fontWeightBold
        }
    }

    component SectionTitle: ColumnLayout {
        id: header
        property string title: ""
        property string purpose: ""
        property string iconName: ""
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            WIcon {
                iconName: header.iconName
                iconSize: 20
                Layout.alignment: Qt.AlignVCenter
            }
            Text {
                text: header.title
                color: "#111111"
                font.family: root.uiFontFamily
                font.pixelSize: root.fontSizeSection
                font.weight: root.fontWeightBold
                Layout.fillWidth: true
            }
            StatusPill {
                status: String(root.currentReadiness.status || "ready")
                label: String(root.currentReadiness.label || root.statusLabel(root.currentReadiness.status || "ready"))
            }
        }
        BodyText {
            text: header.purpose
            color: "#333333"
            font.pixelSize: root.fontSizePurpose
            Layout.fillWidth: true
        }
    }

    component PathField: ColumnLayout {
        id: pathField
        property string label: ""
        property alias text: field.text
        property bool directory: true
        spacing: 6
        Layout.fillWidth: true
        Layout.maximumWidth: 960
        LabelText { text: pathField.label }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            WTextField {
                id: field
                Layout.fillWidth: true
                Layout.preferredHeight: 38
            }
            WButton {
                iconName: pathField.directory ? "folder-open" : "file-search"
                iconOnly: true
                toolTipText: pathField.directory ? root.tr("dialog.choose_folder") : root.tr("dialog.choose_file")
                Layout.preferredWidth: 44
                Layout.minimumWidth: 44
                Layout.maximumWidth: 44
                onClicked: {
                    field.text = root.choosePath(field.text, pathField.directory)
                    root.saveSettings(root.workflowValues())
                }
            }
        }
    }

    component FlowNavButton: Button {
        id: control
        property var definition: ({})
        property var readiness: ({})
        Layout.fillWidth: true
        Layout.preferredHeight: 70
        onClicked: root.currentSection = index
        background: Rectangle {
            color: root.currentSection === index ? "#111111" : "#ffffff"
            border.color: "#111111"
            border.width: root.currentSection === index ? 2 : 1
            radius: 0
        }
        contentItem: RowLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 10
            Text {
                text: String(control.definition.number || "")
                color: root.currentSection === index ? "#ffffff" : "#111111"
                font.family: root.uiFontFamily
                font.pixelSize: root.fontSizeLabel
                font.weight: root.fontWeightBold
                Layout.preferredWidth: 22
            }
            WIcon {
                iconName: String(control.definition.icon_name || "")
                inverted: root.currentSection === index
            }
            ColumnLayout {
                spacing: 4
                Layout.fillWidth: true
                Text {
                    text: String(control.definition.title || "")
                    color: root.currentSection === index ? "#ffffff" : "#111111"
                    font.family: root.uiFontFamily
                    font.pixelSize: root.fontSizeBody
                    font.weight: root.fontWeightBold
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Text {
                    text: root.statusLabel(String(control.readiness.status || "ready"))
                    color: root.currentSection === index ? "#ffffff" : "#555555"
                    font.family: root.uiFontFamily
                    font.pixelSize: root.fontSizeBadge
                    font.weight: root.fontWeightBold
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }
    }

    component Panel: Rectangle {
        id: panel
        default property alias content: panelBody.data
        Layout.fillWidth: true
        color: "#ffffff"
        border.color: "#111111"
        border.width: 2
        radius: 0
        implicitHeight: panelBody.implicitHeight + 40
        ColumnLayout {
            id: panelBody
            anchors.fill: parent
            anchors.margins: 20
            spacing: 16
        }
    }

    component AdvancedBlock: ColumnLayout {
        id: block
        property string title: root.tr("advanced.default")
        property bool open: false
        default property alias content: body.data
        Layout.fillWidth: true
        spacing: 12
        Button {
            id: advancedButton
            text: block.title
            Layout.preferredHeight: 34
            Layout.preferredWidth: 190
            font.family: root.uiFontFamily
            font.pixelSize: root.fontSizeLabel
            font.weight: root.fontWeightBold
            onClicked: block.open = !block.open
            background: Rectangle {
                color: "#ffffff"
                border.color: "#111111"
                border.width: 1
                radius: 0
            }
            contentItem: RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 8
                WIcon {
                    iconName: block.open ? "chevron-down" : "chevron-right"
                }
                Text {
                    text: advancedButton.text
                    color: "#111111"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font: advancedButton.font
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }
        ColumnLayout {
            id: body
            visible: block.open
            Layout.fillWidth: true
            spacing: 12
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 260
            Layout.fillHeight: true
            color: "#ffffff"
            border.color: "#111111"
            border.width: 2

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                Text {
                    text: "wordbyword"
                    color: "#111111"
                    font.family: root.uiFontFamily
                    font.pixelSize: root.fontSizeBrand
                    font.weight: root.fontWeightBold
                    Layout.bottomMargin: 8
                }

                BodyText {
                    text: root.tr("sidebar.guide_flow")
                    color: "#555555"
                    font.pixelSize: root.fontSizeLabel
                    Layout.bottomMargin: 4
                }

                Repeater {
                    model: root.definitions
                    delegate: FlowNavButton {
                        definition: modelData
                        readiness: root.readinessMap[modelData.key] || ({})
                    }
                }

                Item { Layout.fillHeight: true }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: languageBody.implicitHeight + 28
                    color: "#ffffff"
                    border.color: "#111111"
                    border.width: 1
                    ColumnLayout {
                        id: languageBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10
                        LabelText { text: root.tr("language.label") }
                        ComboBox {
                            id: languageCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 38
                            model: root.safeLanguageState.options || []
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: root.languageOptionIndex(String(root.safeLanguageState.selected || "system"))
                            font.family: root.uiFontFamily
                            font.pixelSize: root.fontSizeBody
                            font.weight: root.fontWeightRegular
                            onActivated: function(index) {
                                var options = root.safeLanguageState.options || []
                                if (index >= 0 && index < options.length)
                                    root.setLanguage(String(options[index].value || "system"))
                            }
                            background: Rectangle {
                                color: "#ffffff"
                                border.color: "#111111"
                                border.width: 1
                                radius: 0
                            }
                            contentItem: Text {
                                text: languageCombo.displayText
                                color: "#111111"
                                font: languageCombo.font
                                verticalAlignment: Text.AlignVCenter
                                leftPadding: 10
                                rightPadding: 34
                                elide: Text.ElideRight
                            }
                            indicator: WIcon {
                                iconName: "chevron-down"
                                anchors.right: parent.right
                                anchors.rightMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            popup: Popup {
                                y: languageCombo.height - 1
                                width: languageCombo.width
                                implicitHeight: contentItem.implicitHeight
                                padding: 0
                                contentItem: ListView {
                                    clip: true
                                    implicitHeight: contentHeight
                                    model: languageCombo.popup.visible ? languageCombo.delegateModel : null
                                    currentIndex: languageCombo.highlightedIndex
                                }
                                background: Rectangle {
                                    color: "#ffffff"
                                    border.color: "#111111"
                                    border.width: 1
                                    radius: 0
                                }
                            }
                            delegate: ItemDelegate {
                                width: languageCombo.width
                                height: 34
                                highlighted: languageCombo.highlightedIndex === index
                                background: Rectangle {
                                    color: highlighted ? "#111111" : "#ffffff"
                                }
                                contentItem: Text {
                                    text: modelData.label
                                    color: highlighted ? "#ffffff" : "#111111"
                                    font.family: root.uiFontFamily
                                    font.pixelSize: root.fontSizeBody
                                    font.weight: root.fontWeightRegular
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: 10
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: runtimeBody.implicitHeight + 28
                    color: "#f7f7f4"
                    border.color: "#111111"
                    border.width: 1
                    ColumnLayout {
                        id: runtimeBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10
                        LabelText { text: root.tr("runtime.label") }
                        WTextField {
                            id: modelField
                            Layout.fillWidth: true
                            text: String(root.safeSettings.model || "")
                            onEditingFinished: root.saveSettings(root.workflowValues())
                        }
                        WTextField {
                            id: baseUrlField
                            Layout.fillWidth: true
                            placeholderText: root.tr("placeholder.ollama_base_url")
                            text: String(root.safeSettings.base_url || "")
                            onEditingFinished: root.saveSettings(root.workflowValues())
                        }
                        LabelText { text: "Target grade" }
                        ComboBox {
                            id: targetGradeCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 38
                            model: root.gradeOptions
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: root.gradeOptionIndex(String(root.safeSettings.target_grade || "grade1"))
                            font.family: root.uiFontFamily
                            font.pixelSize: root.fontSizeBody
                            font.weight: root.fontWeightRegular
                            onActivated: root.saveSettings(root.workflowValues())
                            background: Rectangle {
                                color: "#ffffff"
                                border.color: "#111111"
                                border.width: 1
                                radius: 0
                            }
                            contentItem: Text {
                                text: targetGradeCombo.displayText
                                color: "#111111"
                                font: targetGradeCombo.font
                                verticalAlignment: Text.AlignVCenter
                                leftPadding: 10
                                rightPadding: 34
                                elide: Text.ElideRight
                            }
                            indicator: WIcon {
                                iconName: "chevron-down"
                                anchors.right: parent.right
                                anchors.rightMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            ScrollView {
                id: mainScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                leftPadding: 24
                rightPadding: 24
                topPadding: 24
                bottomPadding: 24
                contentWidth: availableWidth

                ColumnLayout {
                    width: Math.min(mainScroll.availableWidth, 1080)
                    x: Math.max(0, (mainScroll.availableWidth - width) / 2)
                    spacing: 28

                    SectionTitle {
                        title: String(root.currentDefinition.number || "") + " / " + String(root.currentDefinition.title || "")
                        purpose: String(root.currentDefinition.purpose || "")
                        iconName: String(root.currentDefinition.icon_name || "")
                        Layout.fillWidth: true
                    }

                    Panel {
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 14
                            LabelText { text: root.tr("panel.end_to_end_flow") }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Repeater {
                                    model: root.definitions
                                    delegate: Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 74
                                        color: root.currentSection === index ? "#111111" : "#f7f7f4"
                                        border.color: "#111111"
                                        border.width: root.currentSection === index ? 2 : 1
                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 10
                                            spacing: 5
                                            WIcon {
                                                iconName: String(modelData.icon_name || "")
                                                inverted: root.currentSection === index
                                                Layout.alignment: Qt.AlignHCenter
                                            }
                                            Text {
                                                text: String(modelData.number || "") + " / " + String(modelData.title || "")
                                                color: root.currentSection === index ? "#ffffff" : "#111111"
                                                font.family: root.uiFontFamily
                                                font.pixelSize: root.fontSizeTiny
                                                font.weight: root.fontWeightBold
                                                wrapMode: Text.WordWrap
                                                horizontalAlignment: Text.AlignHCenter
                                                verticalAlignment: Text.AlignVCenter
                                                Layout.fillWidth: true
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Panel {
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 14
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                LabelText {
                                    text: root.tr("panel.pipeline_stages")
                                    Layout.fillWidth: true
                                }
                                BodyText {
                                    text: String(root.currentReadiness.next_action || "")
                                    color: "#555555"
                                    Layout.maximumWidth: 420
                                    horizontalAlignment: Text.AlignRight
                                }
                            }
                            Repeater {
                                model: root.currentDefinition.stages || []
                                delegate: Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: stageBody.implicitHeight + 22
                                    color: "#ffffff"
                                    border.color: "#111111"
                                    border.width: 1
                                    RowLayout {
                                        id: stageBody
                                        anchors.fill: parent
                                        anchors.margins: 11
                                        spacing: 14
                                        Rectangle {
                                            Layout.preferredWidth: 34
                                            Layout.preferredHeight: 34
                                            color: "#f7f7f4"
                                            border.color: "#111111"
                                            border.width: 1
                                            Text {
                                                anchors.centerIn: parent
                                                text: (index + 1 < 10 ? "0" : "") + String(index + 1)
                                                color: "#111111"
                                                font.family: root.uiFontFamily
                                                font.pixelSize: root.fontSizeTiny
                                                font.weight: root.fontWeightBold
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 5
                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 8
                                                WIcon {
                                                    iconName: String(modelData.icon_name || "")
                                                }
                                                LabelText {
                                                    text: String(modelData.label || "")
                                                    font.pixelSize: root.fontSizeBody
                                                    Layout.fillWidth: true
                                                }
                                            }
                                            BodyText {
                                                text: String(modelData.detail || "")
                                                color: "#333333"
                                                Layout.fillWidth: true
                                            }
                                            BodyText {
                                                visible: Boolean(modelData.output_path_key || modelData.input_path_key)
                                                text: {
                                                    var inputPath = root.pathForStageKey(String(modelData.input_path_key || ""))
                                                    var outputPath = root.pathForStageKey(String(modelData.output_path_key || ""))
                                                    var pieces = []
                                                    if (inputPath)
                                                        pieces.push(String(modelData.input_label || root.tr("common.input")) + ": " + inputPath)
                                                    if (outputPath)
                                                        pieces.push(String(modelData.output_label || root.tr("common.output")) + ": " + outputPath)
                                                    return pieces.join("    ")
                                                }
                                                color: "#666666"
                                                font.pixelSize: root.fontSizeTiny
                                                Layout.fillWidth: true
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Panel {
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 16
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    LabelText { text: root.tr("panel.next_action") }
                                    BodyText {
                                        text: String(root.currentReadiness.detail || "")
                                        color: "#333333"
                                        Layout.fillWidth: true
                                    }
                                    BodyText {
                                        text: String(root.currentReadiness.next_action || "")
                                        color: "#555555"
                                        Layout.fillWidth: true
                                    }
                                }
                                StatusPill {
                                    status: String(root.currentReadiness.status || "ready")
                                    label: String(root.currentReadiness.label || "")
                                }
                            }

                            StackLayout {
                                id: inputStack
                                currentIndex: root.currentSection
                                Layout.fillWidth: true

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: setupDictionaryInput; label: root.tr("dictionary.source_folder"); text: String(root.safeSettings.dictionary_input_dir || "") }
                                    PathField { id: setupClassSource; label: root.tr("class.source_folder"); text: String(root.safeSettings.class_source_dir || "") }
                                }

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: dictionaryInput; label: root.tr("dictionary.source_folder"); text: String(root.safeSettings.dictionary_input_dir || "") }
                                    PathField { id: referenceDictionary; label: root.tr("dictionary.reference_dictionary"); directory: false; text: String(root.safeSettings.reference_dictionary_path || "") }
                                    AdvancedBlock {
                                        title: root.tr("dictionary.advanced_title")
                                        PathField { id: dictionaryOutput; label: root.tr("dictionary.output_folder"); text: String(root.safeSettings.dictionary_output_dir || "") }
                                        RowLayout {
                                            spacing: 18
                                            WCheckBox { id: dictionaryForce; text: root.tr("dictionary.force_rebuild"); checked: Boolean(root.safeSettings.dictionary_force) }
                                            WCheckBox { id: dictionaryNumbers; text: root.tr("dictionary.include_numbers"); checked: Boolean(root.safeSettings.include_numbers) }
                                            WCheckBox { id: dictionaryPunctuation; text: root.tr("dictionary.include_punctuation"); checked: Boolean(root.safeSettings.include_punctuation) }
                                        }
                                    }
                                }

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: classSource; label: root.tr("class.source_folder"); text: String(root.safeSettings.class_source_dir || "") }
                                    AdvancedBlock {
                                        title: root.tr("class.advanced_title")
                                        PathField { id: parsedRoot; label: root.tr("class.parsed_output_folder"); text: String(root.safeSettings.source_output_root || "") }
                                        PathField { id: rewriteRoot; label: root.tr("class.rewrite_output_folder"); text: String(root.safeSettings.rewrite_output_root || "") }
                                        PathField { id: printRoot; label: root.tr("class.print_output_folder"); text: String(root.safeSettings.print_output_root || "") }
                                        PathField { id: rewriteDictionary; label: root.tr("class.override_rewrite_dictionary"); directory: false; text: String(root.safeSettings.rewrite_dictionary_path || "") }
                                        GridLayout {
                                            columns: 2
                                            columnSpacing: 18
                                            rowSpacing: 12
                                            WCheckBox { id: forcePrintouts; text: root.tr("class.force_printouts"); checked: Boolean(root.safeSettings.force_class_printouts) }
                                            WCheckBox { id: skipPdfParse; text: root.tr("class.skip_pdf_parse"); checked: Boolean(root.safeSettings.skip_pdf_parse) }
                                            LabelText { text: root.tr("class.pdf_render_dpi") }
                                            WTextField { id: pdfDpi; text: String(root.safeSettings.pdf_render_dpi || "144"); Layout.preferredWidth: 140 }
                                            LabelText { text: root.tr("class.body_font_size") }
                                            WTextField { id: bodyFontSize; text: String(root.safeSettings.print_body_font_size || ""); Layout.preferredWidth: 140 }
                                        }
                                        PathField { id: printFont; label: root.tr("class.print_font"); directory: false; text: String(root.safeSettings.print_font_path || "") }
                                    }
                                }

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: archiveRoot; label: root.tr("archive.archive_folder"); text: String(root.safeSettings.archive_root || "") }
                                    AdvancedBlock {
                                        title: root.tr("archive.advanced_title")
                                        RowLayout {
                                            spacing: 12
                                            WButton { text: root.tr("archive.preview_rename"); Layout.preferredWidth: 150; onClicked: root.runJob("archive_rename_preview", root.archiveValues()) }
                                            WButton { text: root.tr("archive.apply_rename"); Layout.preferredWidth: 140; onClicked: root.runJob("archive_rename_apply", root.archiveValues()) }
                                        }
                                        WCheckBox { id: includeNearDuplicates; text: root.tr("archive.include_near_duplicates"); checked: Boolean(root.safeSettings.include_near_duplicates) }
                                        RowLayout {
                                            spacing: 12
                                            WButton { text: root.tr("archive.preview_duplicates"); Layout.preferredWidth: 170; onClicked: root.runJob("archive_duplicates_preview", root.archiveValues()) }
                                            WButton { text: root.tr("archive.apply_duplicates"); Layout.preferredWidth: 160; onClicked: root.runJob("archive_duplicates_apply", root.archiveValues()) }
                                        }
                                        PathField { id: usageOutputRoot; label: root.tr("archive.usage_output_folder"); text: String(root.safeSettings.usage_signal_output_root || "") }
                                        RowLayout {
                                            spacing: 18
                                            WCheckBox { id: usageNumbers; text: root.tr("dictionary.include_numbers"); checked: Boolean(root.safeSettings.include_numbers) }
                                            WCheckBox { id: usagePunctuation; text: root.tr("dictionary.include_punctuation"); checked: Boolean(root.safeSettings.include_punctuation) }
                                        }
                                        PathField { id: canonicalDictionary; label: root.tr("archive.canonical_dictionary"); directory: false; text: String(root.safeSettings.canonical_dictionary_path || "") }
                                        PathField { id: usageCandidates; label: root.tr("archive.usage_candidates"); directory: false; text: String(root.safeSettings.usage_candidates_path || "") }
                                        PathField { id: usageSummary; label: root.tr("archive.usage_summary"); directory: false; text: String(root.safeSettings.usage_summary_path || "") }
                                        PathField { id: liveLexicon; label: root.tr("archive.live_lexicon"); directory: false; text: String(root.safeSettings.live_lexicon_path || "") }
                                        PathField { id: supplementalLexicon; label: root.tr("archive.supplemental_lexicon"); directory: false; text: String(root.safeSettings.supplemental_lexicon_path || "") }
                                        PathField { id: unmatchedUsage; label: root.tr("archive.unmatched_usage"); directory: false; text: String(root.safeSettings.unmatched_usage_output_path || "") }
                                    }
                                }

                                ColumnLayout {
                                    spacing: 12
                                    PathField { id: reportPrintout; label: root.tr("reports.archived_printout"); directory: false; text: String(root.safeSettings.after_class_printout_path || "") }
                                    PathField { id: reportOutputRoot; label: root.tr("reports.output_folder"); text: String(root.safeSettings.after_class_report_output_root || "") }
                                    AdvancedBlock {
                                        title: root.tr("reports.advanced_title")
                                        PathField { id: reportFont; label: root.tr("reports.report_font"); directory: false; text: String(root.safeSettings.after_class_report_font_path || "") }
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                WButton {
                                    text: String(root.currentDefinition.primary_action_label || root.tr("button.save"))
                                    iconName: root.actionIcon(String(root.currentDefinition.primary_workflow_id || ""))
                                    primary: true
                                    Layout.preferredWidth: 220
                                    onClicked: root.runWorkflow(String(root.currentDefinition.primary_workflow_id || ""))
                                }
                                WButton {
                                    visible: Boolean(root.currentDefinition.secondary_workflow_id)
                                    text: String(root.currentDefinition.secondary_action_label || "")
                                    iconName: root.actionIcon(String(root.currentDefinition.secondary_workflow_id || ""))
                                    Layout.preferredWidth: 190
                                    onClicked: root.runWorkflow(String(root.currentDefinition.secondary_workflow_id || ""))
                                }
                                WButton {
                                    text: root.tr("button.refresh_status")
                                    iconName: "refresh-cw"
                                    Layout.preferredWidth: 140
                                    onClicked: root.saveSettings(root.workflowValues())
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: root.runDrawerOpen ? 236 : 72
                color: "#ffffff"
                border.color: "#111111"
                border.width: 2

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        StatusPill {
                            status: String(root.safeJobState.status || "idle")
                            label: root.statusLabel(String(root.safeJobState.status || "idle"))
                        }
                        BodyText {
                            text: String(root.safeJobState.detail || root.tr("app.ready_detail"))
                            color: "#333333"
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            maximumLineCount: 1
                        }
                        WButton {
                            text: root.runDrawerOpen ? root.tr("run.hide_details") : root.tr("run.show_details")
                            iconName: root.runDrawerOpen ? "panel-bottom-close" : "panel-bottom-open"
                            Layout.preferredWidth: 160
                            enabled: true
                            onClicked: root.runDrawerOpen = !root.runDrawerOpen
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 10
                        color: "#f7f7f4"
                        border.color: "#111111"
                        border.width: 1
                        Rectangle {
                            height: parent.height
                            width: {
                                var total = Number(root.safeJobState.total || 0)
                                var current = Number(root.safeJobState.current || 0)
                                if (total <= 0)
                                    return root.busy ? parent.width * 0.25 : 0
                                return Math.max(0, Math.min(parent.width, parent.width * current / total))
                            }
                            color: "#111111"
                        }
                    }

                    RowLayout {
                        visible: root.runDrawerOpen
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 12

                        TextArea {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            readOnly: true
                            text: root.safeLogText || root.tr("run.no_log")
                            wrapMode: TextArea.Wrap
                            color: "#111111"
                            font.family: root.monoFontFamily
                            font.pixelSize: root.fontSizeTiny
                            background: Rectangle {
                                color: "#ffffff"
                                border.color: "#111111"
                                border.width: 1
                                radius: 0
                            }
                        }

                        TextArea {
                            Layout.preferredWidth: 360
                            Layout.fillHeight: true
                            readOnly: true
                            text: root.safeArtifactText
                            wrapMode: TextArea.Wrap
                            color: "#111111"
                            font.family: root.monoFontFamily
                            font.pixelSize: root.fontSizeTiny
                            background: Rectangle {
                                color: "#ffffff"
                                border.color: "#111111"
                                border.width: 1
                                radius: 0
                            }
                        }
                    }
                }
            }
        }
    }
}
