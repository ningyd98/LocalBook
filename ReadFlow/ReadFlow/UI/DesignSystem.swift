//
//  DesignSystem.swift
//  ReadFlow
//
//  The visual vocabulary the interface is built from: colours, type scale,
//  spacing, and four small components (badge, card, status dot, empty state).
//
//  Why a file rather than inline styling: the surfaces were each inventing their
//  own padding, corner radius and caption size, so the same "secondary metadata"
//  looked like three different things depending on which window you were in.
//  Everything visual that appears more than once lives here.
//
//  The one non-visual thing that lives here is `RFDate.humanised`, because a raw
//  ISO-8601 string is a data format, not a design: an interface should not show
//  `2026-09-13T05:22:56Z` to a person. It never invents a date — an unparseable
//  value is returned unchanged, so a formatting failure shows the real value
//  instead of a plausible wrong one.
//

import SwiftUI

// MARK: - Tokens

enum RFColor {
    /// Matches the app icon's deep indigo, so the icon and the UI agree.
    static let accent = Color(red: 0.35, green: 0.42, blue: 0.94)
    static let highlight = Color(red: 0.98, green: 0.66, blue: 0.13)
    static let note = Color(red: 0.25, green: 0.55, blue: 0.95)
    static let readerItem = Color(red: 0.13, green: 0.68, blue: 0.62)
    static let warning = Color(red: 0.90, green: 0.55, blue: 0.10)
    static let danger = Color(red: 0.86, green: 0.28, blue: 0.26)
    static let success = Color(red: 0.20, green: 0.68, blue: 0.40)

    static let cardBackground = Color(nsColor: .controlBackgroundColor)
    static let fieldBackground = Color.primary.opacity(0.05)
    static let hairline = Color.primary.opacity(0.08)
    static let hover = Color.primary.opacity(0.045)
}

enum RFMetric {
    static let radius: CGFloat = 9
    static let fieldRadius: CGFloat = 8
    static let gutter: CGFloat = 14
    static let rowGap: CGFloat = 6
    /// Fixed label column, so a long label wraps inside its own column instead of
    /// squeezing the field (the provider pane shipped with labels that wrapped
    /// across two lines and collided with their inputs).
    static let labelColumn: CGFloat = 132
}

// MARK: - Components

/// A tone describes meaning, not colour — the colour is chosen here so a status
/// reads the same everywhere it appears.
enum RFTone {
    case neutral, accent, warning, danger, success

    var foreground: Color {
        switch self {
        case .neutral: return .secondary
        case .accent: return RFColor.accent
        case .warning: return RFColor.warning
        case .danger: return RFColor.danger
        case .success: return RFColor.success
        }
    }
}

struct RFBadge: View {
    let text: String
    var tone: RFTone = .neutral
    var systemImage: String?

    var body: some View {
        HStack(spacing: 4) {
            if let systemImage {
                Image(systemName: systemImage).font(.system(size: 9, weight: .semibold))
            }
            Text(text).font(.system(size: 10.5, weight: .semibold))
        }
        .foregroundStyle(tone.foreground)
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(tone.foreground.opacity(0.12), in: Capsule())
    }
}

struct RFStatusDot: View {
    var tone: RFTone
    var body: some View {
        Circle().fill(tone.foreground).frame(width: 8, height: 8)
    }
}

/// A grouped surface. Cards give the settings surfaces a spine: related controls
/// sit together and unrelated ones visibly do not.
struct RFCard<Content: View>: View {
    var title: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title {
                Text(title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
                    .kerning(0.4)
            }
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RFColor.cardBackground, in: RoundedRectangle(cornerRadius: RFMetric.radius))
        .overlay(
            RoundedRectangle(cornerRadius: RFMetric.radius)
                .stroke(RFColor.hairline, lineWidth: 1)
        )
    }
}

struct RFEmptyState: View {
    let systemImage: String
    let title: String
    let message: String
    var footnote: String?

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.system(size: 22, weight: .regular))
                .foregroundStyle(.tertiary)
                .frame(width: 46, height: 46)
                .background(Color.primary.opacity(0.05), in: Circle())

            Text(title)
                .font(.system(size: 13, weight: .semibold))

            Text(message)
                .font(.system(size: 11.5))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            if let footnote {
                Text(footnote)
                    .font(.system(size: 10.5))
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: 320)
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// A single field row: fixed label column, control fills the rest, optional help
/// line underneath. Used by every settings surface so rows line up across panes.
struct RFFieldRow<Control: View>: View {
    let label: String
    var help: String?
    var warning: String?
    @ViewBuilder var control: Control

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(label)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .frame(width: RFMetric.labelColumn, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)

                control.frame(maxWidth: .infinity, alignment: .leading)
            }
            if let warning {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 10))
                        .foregroundStyle(RFColor.warning)
                    Text(warning)
                        .font(.system(size: 10.5))
                        .foregroundStyle(RFColor.warning)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.leading, RFMetric.labelColumn + 12)
            }
            if let help {
                Text(help)
                    .font(.system(size: 10.5))
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, RFMetric.labelColumn + 12)
            }
        }
    }
}

// MARK: - Dates

enum RFDate {
    private static let internet: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let internetFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let clock: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_Hans_CN")
        f.dateFormat = "HH:mm"
        return f
    }()

    private static let monthDay: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_Hans_CN")
        f.dateFormat = "M月d日"
        return f
    }()

    private static let fullDate: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_Hans_CN")
        f.dateFormat = "yyyy年M月d日"
        return f
    }()

    /// A date a person can read: 今天 13:22 / 昨天 09:05 / 9月12日 / 2025年12月1日.
    ///
    /// Returns the input untouched when it cannot be parsed. Inventing a plausible
    /// date for an unparseable value would be exactly the kind of fabricated
    /// content this codebase refuses elsewhere.
    static func humanised(_ raw: String, now: Date = Date()) -> String {
        guard let date = internet.date(from: raw) ?? internetFractional.date(from: raw) else {
            return raw
        }
        let calendar = Calendar.current
        if calendar.isDate(date, inSameDayAs: now) {
            return "今天 " + clock.string(from: date)
        }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
           calendar.isDate(date, inSameDayAs: yesterday) {
            return "昨天 " + clock.string(from: date)
        }
        if calendar.component(.year, from: date) == calendar.component(.year, from: now) {
            return monthDay.string(from: date)
        }
        return fullDate.string(from: date)
    }
}
