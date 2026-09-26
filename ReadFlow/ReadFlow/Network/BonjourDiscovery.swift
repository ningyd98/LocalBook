//
//  BonjourDiscovery.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import Network

class BonjourDiscovery: ObservableObject {
    @Published var discoveredServices: [LocalBookService] = []
    @Published var isDiscovering: Bool = false

    private var browser: NWBrowser?
    private var resolvers: [UUID: NWConnection] = [:]

    // MARK: - Public API

    func startDiscovery() {
        guard !isDiscovering else { return }

        isDiscovering = true

        let parameters = NWParameters()
        parameters.includePeerToPeer = true

        // Browse for _localbook._tcp service
        browser = NWBrowser(for: .bonjour(type: "_localbook._tcp", domain: nil), using: parameters)

        browser?.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                print("[Bonjour] Browser ready")
            case .failed(let error):
                print("[Bonjour] Browser failed: \(error)")
                self?.isDiscovering = false
            case .cancelled:
                self?.isDiscovering = false
            default:
                break
            }
        }

        browser?.browseResultsChangedHandler = { [weak self] results, changes in
            self?.handleBrowseResults(results, changes: changes)
        }

        browser?.start(queue: .main)
    }

    func stopDiscovery() {
        browser?.cancel()
        browser = nil

        // Cancel all resolvers
        for (_, connection) in resolvers {
            connection.cancel()
        }
        resolvers.removeAll()

        isDiscovering = false
    }

    // MARK: - Private Methods

    private func handleBrowseResults(_ results: Set<NWBrowser.Result>, changes: Set<NWBrowser.Result.Change>) {
        for change in changes {
            switch change {
            case .added(let result):
                resolveService(result)
            case .removed(let result):
                removeService(result)
            default:
                break
            }
        }
    }

    private func resolveService(_ result: NWBrowser.Result) {
        guard case .service(let name, let type, let domain, let interface) = result.endpoint else {
            return
        }

        let serviceID = UUID()

        // Create connection to resolve the service
        let connection = NWConnection(to: result.endpoint, using: .tcp)

        connection.stateUpdateHandler = { [weak self] state in
            guard let self = self else { return }

            switch state {
            case .ready:
                if let endpoint = connection.currentPath?.remoteEndpoint {
                    self.handleResolvedEndpoint(
                        serviceID: serviceID,
                        name: name,
                        type: type,
                        domain: domain,
                        interface: interface,
                        endpoint: endpoint
                    )
                }
                connection.cancel()
                self.resolvers.removeValue(forKey: serviceID)

            case .failed(let error):
                print("[Bonjour] Failed to resolve service '\(name)': \(error)")
                connection.cancel()
                self.resolvers.removeValue(forKey: serviceID)

            case .cancelled:
                self.resolvers.removeValue(forKey: serviceID)

            default:
                break
            }
        }

        resolvers[serviceID] = connection
        connection.start(queue: .main)
    }

    private func handleResolvedEndpoint(
        serviceID: UUID,
        name: String,
        type: String,
        domain: String?,
        interface: NWInterface?,
        endpoint: NWEndpoint
    ) {
        guard case .hostPort(let host, let port) = endpoint else {
            print("[Bonjour] Resolved endpoint is not hostPort")
            return
        }

        let hostString: String
        switch host {
        case .ipv4(let address):
            hostString = address.debugDescription
        case .ipv6(let address):
            hostString = "[\(address.debugDescription)]"
        case .name(let hostname, _):
            hostString = hostname
        @unknown default:
            hostString = host.debugDescription
        }

        let portInt = Int(port.rawValue)
        let url = URL(string: "http://\(hostString):\(portInt)")

        let service = LocalBookService(
            id: serviceID,
            name: name,
            type: type,
            domain: domain,
            interface: interface,
            host: hostString,
            port: portInt,
            resolvedURL: url
        )

        DispatchQueue.main.async {
            // Update or add service
            if let index = self.discoveredServices.firstIndex(where: { $0.name == name }) {
                self.discoveredServices[index] = service
            } else {
                self.discoveredServices.append(service)
            }
            print("[Bonjour] Discovered service: \(name) at \(hostString):\(portInt)")
        }
    }

    private func removeService(_ result: NWBrowser.Result) {
        guard case .service(let name, _, _, _) = result.endpoint else {
            return
        }

        DispatchQueue.main.async {
            self.discoveredServices.removeAll { $0.name == name }
            print("[Bonjour] Removed service: \(name)")
        }
    }

    deinit {
        stopDiscovery()
    }
}

// MARK: - Models

struct LocalBookService: Identifiable {
    let id: UUID
    let name: String
    let type: String
    let domain: String?
    let interface: NWInterface?
    let host: String
    let port: Int
    var resolvedURL: URL?

    var displayName: String {
        return name
    }

    var displayAddress: String {
        return "\(host):\(port)"
    }
}
