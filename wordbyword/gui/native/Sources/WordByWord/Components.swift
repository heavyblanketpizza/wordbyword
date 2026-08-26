import SwiftUI

struct FolderTabShape: InsettableShape {
    var insetAmount: CGFloat = 0

    func path(in rect: CGRect) -> Path {
        let bounds = rect.insetBy(dx: insetAmount, dy: insetAmount)
        let radius = min(8, max(0, bounds.height / 2))
        var path = Path()
        path.move(to: CGPoint(x: bounds.minX, y: bounds.maxY))
        path.addLine(to: CGPoint(x: bounds.minX, y: bounds.minY + radius))
        path.addQuadCurve(
            to: CGPoint(x: bounds.minX + radius, y: bounds.minY),
            control: CGPoint(x: bounds.minX, y: bounds.minY)
        )
        path.addLine(to: CGPoint(x: bounds.maxX - radius, y: bounds.minY))
        path.addQuadCurve(
            to: CGPoint(x: bounds.maxX, y: bounds.minY + radius),
            control: CGPoint(x: bounds.maxX, y: bounds.minY)
        )
        path.addLine(to: CGPoint(x: bounds.maxX, y: bounds.maxY))
        path.closeSubpath()
        return path
    }

    func inset(by amount: CGFloat) -> FolderTabShape {
        var shape = self
        shape.insetAmount += amount
        return shape
    }
}

struct MonochromeButtonStyle: ButtonStyle {
    var primary = false
    var compact = false

    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.isFocused) private var isFocused
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.studioLanguage) private var language

    init(primary: Bool = false, compact: Bool = false) {
        self.primary = primary
        self.compact = compact
    }

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: 6, style: .continuous)
        let depression: CGFloat = primary && configuration.isPressed ? 1.5 : 0
        configuration.label
            .font(StudioType.mono(compact ? 11.5 : 13.75, weight: .semibold, language: language))
            .padding(.horizontal, compact ? 11 : 17)
            .frame(height: compact ? 32 : 44)
            .foregroundStyle(primary ? StudioTheme.canvas : StudioTheme.ink)
            .background {
                shape.fill(primary ? StudioTheme.ink :
                    (configuration.isPressed ? StudioTheme.faint : StudioTheme.canvas))
            }
            .overlay { shape.strokeBorder(StudioTheme.ink, lineWidth: 1.25) }
            .offset(x: depression, y: depression)
            .background {
                if primary {
                    shape.fill(StudioTheme.ink).offset(x: 2.5, y: 2.5)
                }
            }
            .overlay {
                shape.stroke(StudioTheme.ink, lineWidth: 2)
                    .padding(-3)
                    .opacity(isFocused ? 1 : 0)
            }
            .padding(.trailing, primary ? 2.5 : 0)
            .padding(.bottom, primary ? 2.5 : 0)
            .opacity(isEnabled ? 1 : 0.42)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.10), value: configuration.isPressed)
    }
}

struct IconButton: View {
    let title: String
    let systemName: String
    let action: () -> Void

    init(title: String, systemName: String, action: @escaping () -> Void) {
        self.title = title
        self.systemName = systemName
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 13, weight: .semibold))
                .frame(width: 13, height: 16)
        }
        .buttonStyle(MonochromeButtonStyle(compact: true))
        .help(title)
        .accessibilityLabel(title)
    }
}

struct StatusBadge: View {
    @Environment(\.studioLanguage) private var language
    let text: String
    var filled: Bool

    init(_ text: String, filled: Bool = false) {
        self.text = text
        self.filled = filled
    }

    var body: some View {
        Text(text)
            .font(StudioType.condensed(10.5, weight: .medium, language: language))
            .tracking(0.2)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .foregroundStyle(filled ? StudioTheme.canvas : StudioTheme.ink)
            .background(filled ? StudioTheme.ink : StudioTheme.canvas)
            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(StudioTheme.ink, lineWidth: 1)
            }
            .accessibilityLabel(text)
    }
}

struct SectionLabel: View {
    @Environment(\.studioLanguage) private var language
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text.uppercased())
            .font(StudioType.condensed(11.25, weight: .bold, language: language))
            .tracking(0.55)
            .foregroundStyle(StudioTheme.muted)
            .accessibilityLabel(text)
    }
}

struct StudioPanel<Content: View>: View {
    let title: String
    let icon: String?
    let badge: String?
    private let content: Content

    init(
        title: String,
        icon: String? = nil,
        badge: String? = nil,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.icon = icon
        self.badge = badge
        self.content = content()
    }

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 10, style: .continuous)
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                if let icon {
                    Image(systemName: icon)
                        .font(.system(size: 13.5, weight: .semibold))
                        .frame(width: 18)
                        .accessibilityHidden(true)
                }
                Text(title.uppercased())
                    .font(.system(size: 13.5, weight: .bold))
                    .lineLimit(1)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 8)
                if let badge { StatusBadge(badge) }
            }
            .padding(.horizontal, 14)
            .frame(minHeight: 44)

            Rectangle().fill(StudioTheme.ink).frame(height: 1.5)
            content
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .foregroundStyle(StudioTheme.ink)
        .background(StudioTheme.canvas)
        .clipShape(shape)
        .overlay { shape.strokeBorder(StudioTheme.ink, lineWidth: 1.5) }
        .background { shape.fill(StudioTheme.ink).offset(x: 3, y: 3) }
        .padding(.trailing, 3)
        .padding(.bottom, 3)
        .accessibilityElement(children: .contain)
    }
}

struct EmptyState: View {
    @Environment(\.studioLanguage) private var language
    let icon: String
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 28, weight: .regular))
                .accessibilityHidden(true)
            Text(title)
                .font(StudioType.heading(in: language))
                .accessibilityAddTraits(.isHeader)
            Text(detail)
                .font(StudioType.body)
                .foregroundStyle(StudioTheme.muted)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(StudioTheme.ink)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct MonoToggle: View {
    let title: String
    @Binding var isOn: Bool
    let detail: String?

    init(_ title: String, isOn: Binding<Bool>, detail: String? = nil) {
        self.title = title
        _isOn = isOn
        self.detail = detail
    }

    var body: some View {
        Button { isOn.toggle() } label: {
            HStack(alignment: .top, spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 3, style: .continuous)
                        .fill(isOn ? StudioTheme.ink : StudioTheme.canvas)
                    RoundedRectangle(cornerRadius: 3, style: .continuous)
                        .strokeBorder(StudioTheme.ink, lineWidth: 1.5)
                    if isOn {
                        Image(systemName: "checkmark")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(StudioTheme.canvas)
                    }
                }
                .frame(width: 20, height: 20)
                .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.system(size: 14, weight: .medium))
                    if let detail, !detail.isEmpty {
                        Text(detail)
                            .font(StudioType.detail)
                            .foregroundStyle(StudioTheme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 1)
            }
            .foregroundStyle(StudioTheme.ink)
            .contentShape(Rectangle())
        }
        .buttonStyle(CheckboxButtonStyle())
        .accessibilityRepresentation {
            Toggle(title, isOn: $isOn)
                .toggleStyle(.checkbox)
                .accessibilityHint(detail ?? "")
        }
    }
}

private struct CheckboxButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.isFocused) private var isFocused

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(isEnabled ? (configuration.isPressed ? 0.65 : 1) : 0.42)
            .overlay {
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .stroke(StudioTheme.ink, lineWidth: 2)
                    .padding(-4)
                    .opacity(isFocused ? 1 : 0)
            }
    }
}
