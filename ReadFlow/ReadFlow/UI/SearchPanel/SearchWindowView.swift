//
//  SearchWindowView.swift
//  ReadFlow
//
//  The standalone local search window (contract C8, C25 §4).
//
//  Why this is a plain search window and not a "server-first, local-fallback"
//  one: the server's `/search` is an empty stub that returns no rows
//  (contract §2.1), so treating it as a superior source would make the feature
//  look broken while silently discarding results that are already on disk. C8
//  therefore forbids the fallback shape entirely — this view calls
//  `ReaderDatabase.searchLocal` and nothing else, and issues **no network
//  request at all** (A11).
//
//  Display rules come from `LocalSearchResult`'s accessors rather than being
//  re-derived here (C25 §4). That is deliberate: the `render-search-row` outlet
//  prints those same accessor values, so its output is evidence about *this*
//  view's operands only because this view does not own a second copy of the
//  rules. Re-deriving the gating here would demote that outlet to "two
//  implementations agree".
//
//  The search behaviour lives in `LocalSearchViewModel` rather than in the view
//  body for a second reason: A14's third layer (retrieval issues zero outbound
//  requests) needs a **scriptable** caller, and the interface cannot be observed
//  in this environment. The `search-query` self-test drives this same view
//  model, so what it measures is the window's real code path, not a copy.
//

import SwiftUI

/// The search behaviour, extracted from the view so it can be driven from
/// outside SwiftUI.
///
/// The split is what makes the scriptable outlet meaningful: `run(query:)` is
/// the single place that talks to the store, and both the window and the
/// `search-query` self-test call it.
@MainActor
final class LocalSearchViewModel: ObservableObject {

    enum State: Equatable {
        /// No query yet, or a query that matched nothing. The view distinguishes
        /// those with its own copy, not by the state.
        case empty
        case results([LocalSearchResult])
        case failure(String)
    }

    @Published private(set) var state: State = .empty

    private let database: ReaderDatabase?

    init(database: ReaderDatabase? = ReaderDatabase.shared) {
        self.database = database
    }

    /// Runs a query and publishes the outcome.
    ///
    /// Local only by construction: the sole dependency is `ReaderDatabase`, and
    /// `searchLocal` reads the on-disk FTS5 index. There is no client, no URL and
    /// no network call anywhere in this method — which is what the `search-query`
    /// outlet then *measures* rather than assumes.
    @discardableResult
    func run(query: String) -> State {
        guard let database else {
            state = .failure("本地库不可用")
            return state
        }

        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            state = .empty
            return state
        }

        do {
            let results = try database.searchLocal(query: trimmed)
            state = results.isEmpty ? .empty : .results(results)
        } catch {
            state = .failure(error.localizedDescription)
        }
        return state
    }
}

struct SearchWindowView: View {
    @StateObject private var model: LocalSearchViewModel

    @State private var query: String = ""

    /// Injected so the view is constructible in previews and self-tests without
    /// touching the shared store.
    ///
    /// `initialQuery` exists for `--render-ui`: `ImageRenderer` never runs
    /// `onAppear`, so a state that is only reachable by typing cannot be
    /// snapshotted. Running the query in the initialiser makes the results state
    /// renderable — and the default keeps every existing call site unchanged.
    @MainActor
    init(database: ReaderDatabase? = ReaderDatabase.shared, initialQuery: String = "") {
        let model = LocalSearchViewModel(database: database)
        if !initialQuery.isEmpty {
            model.run(query: initialQuery)
        }
        _model = StateObject(wrappedValue: model)
        _query = State(initialValue: initialQuery)
    }

    /// Presentation-only accessors over the model state, so the body below owns
    /// no search rules of its own.
    private var results: [LocalSearchResult] {
        if case .results(let rows) = model.state { return rows }
        return []
    }

    private var errorMessage: String? {
        if case .failure(let message) = model.state { return message }
        return nil
    }

    private var isQueryBlank: Bool {
        query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            searchField

            if !results.isEmpty { metaBar }

            Divider()

            content
        }
        .frame(minWidth: 460, minHeight: 340)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    // MARK: - Search field

    /// Results header. Cheap, and it answers "did it search?" without the user
    /// having to count rows or wonder whether the query ran at all.
    private var metaBar: some View {
        HStack(spacing: 6) {
            Text("\(results.count) 条结果")
                .font(.system(size: 10.5, weight: .medium))
                .foregroundStyle(.secondary)
            Text("·").font(.system(size: 10.5)).foregroundStyle(.tertiary)
            Text("本地检索，无需网络")
                .font(.system(size: 10.5))
                .foregroundStyle(.tertiary)
            Spacer()
        }
        .padding(.horizontal, RFMetric.gutter)
        .padding(.bottom, 8)
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)

            TextField("搜索高亮、笔记与阅读条目", text: $query)
                .textFieldStyle(.plain)
                .onSubmit { model.run(query: query) }
                // Live search keeps the window useful without a second control;
                // the work is local and cheap, and a blank query collapses to
                // `.empty` inside `run(query:)`.
                .onChange(of: query) { _ in model.run(query: query) }

            if !isQueryBlank {
                Button {
                    query = ""
                    model.run(query: query)
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help("清除")
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(RFColor.fieldBackground, in: RoundedRectangle(cornerRadius: RFMetric.fieldRadius))
        .overlay(
            RoundedRectangle(cornerRadius: RFMetric.fieldRadius)
                .stroke(RFColor.hairline, lineWidth: 1)
        )
        .padding(.horizontal, RFMetric.gutter)
        .padding(.vertical, 10)
    }

    // MARK: - Results

    @ViewBuilder
    private var content: some View {
        if let errorMessage {
            // A failing store is surfaced, never rendered as "no results": the
            // two states send the user to different actions.
            VStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 28))
                    .foregroundColor(.orange)
                Text("本地检索失败")
                    .font(.headline)
                Text(errorMessage)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding()
        } else if results.isEmpty {
            emptyState
        } else {
            resultList
        }
    }

    private var emptyState: some View {
        // "Nothing typed yet" and "nothing matched" need different next actions,
        // so they are different screens rather than one message that fits neither.
        if isQueryBlank {
            RFEmptyState(systemImage: "text.magnifyingglass",
                         title: "搜索本地内容",
                         message: "输入关键字，检索你的高亮、笔记与阅读条目。",
                         footnote: "检索完全在本地进行，不需要服务器或网络。")
        } else {
            RFEmptyState(systemImage: "questionmark.circle",
                         title: "没有找到「\(query.trimmingCharacters(in: .whitespacesAndNewlines))」",
                         message: "试试更短的关键字，或换一个词。",
                         footnote: "检索覆盖全部已保存的高亮、笔记与阅读条目。")
        }
    }

    private var resultList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(Array(results.enumerated()), id: \.element.id) { index, result in
                    SearchResultRow(result: result)
                    if index < results.count - 1 {
                        Divider()
                            .padding(.leading, RFMetric.gutter)
                    }
                }
            }
        }
    }
}

/// One row. Every displayed string comes from `LocalSearchResult`'s accessors.
struct SearchResultRow: View {
    let result: LocalSearchResult

    @State private var hovering = false

    /// Kind colour, not kind text: the label already says which kind it is, so
    /// the colour only has to make the three kinds scannable at a glance.
    private var kindTone: RFTone {
        switch result.type {
        case .highlight: return .warning
        case .note: return .accent
        case .readerItem: return .success
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: RFMetric.rowGap) {
            Text(result.displayContent)
                .font(.system(size: 13, weight: .medium))
                .lineLimit(2)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            if let context = result.displayContext {
                Text(context)
                    .font(.system(size: 11.5))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 8) {
                RFBadge(text: result.displayKind, tone: kindTone)

                // `displayTitle` already falls back to 「未知来源」 for a missing
                // or blank title, so no placeholder logic is repeated here.
                Text(result.displayTitle)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                Spacer(minLength: 8)

                // The date element is omitted entirely when absent — not rendered
                // empty. `displaysDate` is the decision, `displayDate` the value,
                // so an empty `Text` can never be constructed here.
                if result.displaysDate, let date = result.displayDate {
                    Text(date)
                        .font(.system(size: 10.5))
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .padding(.horizontal, RFMetric.gutter)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(hovering ? RFColor.hover : Color.clear)
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
    }
}

#if DEBUG
struct SearchWindowView_Previews: PreviewProvider {
    static var previews: some View {
        SearchWindowView(database: nil)
    }
}
#endif
